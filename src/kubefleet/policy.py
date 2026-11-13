"""Admission policies for Kubernetes manifests, evaluated on the parsed objects.

The checks follow Kubernetes semantics rather than the text of the YAML:
- every workload kind is unwrapped to its pod spec (Pod; Deployment, StatefulSet, DaemonSet, ReplicaSet, Job;
  CronJob's `spec.jobTemplate.spec.template`);
- containers, init containers and ephemeral containers are all checked;
- a container's `securityContext` overrides the pod's, field by field (`runAsNonRoot`, `runAsUser`);
- image references are parsed: `registry:5000/app` has no tag, `app@sha256:...` is pinned;
- probes are required only for long-running workloads, not Jobs or CronJobs;
- RBAC Roles and ClusterRoles are checked for wildcard verbs or resources.

`naive` is the shortcut it is compared with: searching the YAML text for the tell-tale strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

RULES = {
    "privileged": ("high", "Container runs privileged", "Set securityContext.privileged to false."),
    "run_as_root": (
        "high",
        "Container may run as root",
        "Set runAsNonRoot: true (pod or container) and a non-zero runAsUser.",
    ),
    "privilege_escalation": ("medium", "Privilege escalation not disabled", "Set allowPrivilegeEscalation: false."),
    "dangerous_capabilities": (
        "high",
        "Adds dangerous Linux capabilities",
        "Remove SYS_ADMIN, NET_ADMIN and ALL from capabilities.add.",
    ),
    "no_limits": ("medium", "Missing CPU or memory limits", "Set resources.limits.cpu and resources.limits.memory."),
    "no_requests": (
        "medium",
        "Missing CPU or memory requests",
        "Set resources.requests so the scheduler can place it.",
    ),
    "unpinned_image": ("medium", "Image is :latest or untagged", "Use a version tag or a digest."),
    "host_path": ("high", "Mounts a hostPath volume", "Use a PersistentVolumeClaim or emptyDir instead."),
    "host_namespaces": (
        "high",
        "Shares the host network, PID or IPC namespace",
        "Remove hostNetwork, hostPID and hostIPC.",
    ),
    "no_probes": (
        "low",
        "Long-running container without readiness or liveness probe",
        "Add readinessProbe and livenessProbe.",
    ),
    "rbac_wildcard": ("high", "RBAC rule grants * verbs or resources", "List the verbs and resources needed."),
}
DANGEROUS_CAPS = {"SYS_ADMIN", "NET_ADMIN", "ALL", "SYS_PTRACE", "SYS_MODULE"}
LONG_RUNNING = {"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}
WORKLOADS = LONG_RUNNING | {"Job", "CronJob"}


@dataclass(frozen=True)
class Violation:
    rule: str
    obj: str  # Kind/name
    container: str | None
    message: str

    @property
    def severity(self) -> str:
        return RULES[self.rule][0]


def _truthy(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def pod_spec(obj: dict[str, Any]) -> dict[str, Any] | None:
    kind = obj.get("kind")
    spec = obj.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"):
        return ((spec.get("template") or {}).get("spec")) or {}
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
    return None


def image_pinned(image: str) -> bool:
    if "@sha256:" in image:
        return True
    last = image.rsplit("/", 1)[-1]  # a registry port (host:5000/app) is not a tag
    if ":" not in last:
        return False
    return last.rsplit(":", 1)[1] != "latest"


def check_object(obj: dict[str, Any]) -> list[Violation]:
    kind = str(obj.get("kind", ""))
    name = f"{kind}/{(obj.get('metadata') or {}).get('name', '?')}"
    out: list[Violation] = []

    def add(rule: str, container: str | None = None) -> None:
        out.append(Violation(rule, name, container, RULES[rule][1]))

    if kind in ("Role", "ClusterRole"):
        for rule in obj.get("rules") or []:
            if "*" in (rule.get("verbs") or []) or "*" in (rule.get("resources") or []):
                add("rbac_wildcard")
                break
        return out
    spec = pod_spec(obj)
    if spec is None:
        return out
    if any(_truthy(spec.get(k)) for k in ("hostNetwork", "hostPID", "hostIPC")):
        add("host_namespaces")
    if any("hostPath" in (v or {}) for v in spec.get("volumes") or []):
        add("host_path")
    pod_sc = spec.get("securityContext") or {}
    groups = [
        ("containers", spec.get("containers") or []),
        ("initContainers", spec.get("initContainers") or []),
        ("ephemeralContainers", spec.get("ephemeralContainers") or []),
    ]
    for group, containers in groups:
        for c in containers:
            cname = str(c.get("name", "?"))
            sc = c.get("securityContext") or {}
            if _truthy(sc.get("privileged")):
                add("privileged", cname)
            non_root = sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot"))
            user = sc.get("runAsUser", pod_sc.get("runAsUser"))
            if user == 0 or str(user) == "0" or not _truthy(non_root):
                add("run_as_root", cname)
            if group != "ephemeralContainers" and sc.get("allowPrivilegeEscalation") not in (False, "false", "False"):
                add("privilege_escalation", cname)
            caps = {str(x).upper() for x in ((sc.get("capabilities") or {}).get("add") or [])}
            if caps & DANGEROUS_CAPS:
                add("dangerous_capabilities", cname)
            if group == "ephemeralContainers":
                if not image_pinned(str(c.get("image", ""))):
                    add("unpinned_image", cname)
                continue
            res = c.get("resources") or {}
            limits, requests = res.get("limits") or {}, res.get("requests") or {}
            if not ("cpu" in limits and "memory" in limits):
                add("no_limits", cname)
            if not ("cpu" in requests and "memory" in requests):
                add("no_requests", cname)
            if not image_pinned(str(c.get("image", ""))):
                add("unpinned_image", cname)
            if (
                group == "containers"
                and kind in LONG_RUNNING
                and not (c.get("readinessProbe") or c.get("livenessProbe"))
            ):
                add("no_probes", cname)
    return out


def check(text: str) -> list[Violation]:
    """All violations in a (possibly multi-document) YAML text."""
    out: list[Violation] = []
    for doc in yaml.safe_load_all(text):
        if isinstance(doc, dict):
            if doc.get("kind") == "List":
                for item in doc.get("items") or []:
                    if isinstance(item, dict):
                        out += check_object(item)
            else:
                out += check_object(doc)
    return out


def naive(text: str) -> set[str]:
    """The shortcut: which rules does the YAML text appear to break, by searching for strings?"""
    found = set()
    if re.search(r"privileged:\s*true", text):
        found.add("privileged")
    if "runAsNonRoot: true" not in text or re.search(r"runAsUser:\s*0\b", text):
        found.add("run_as_root")
    if "allowPrivilegeEscalation: false" not in text and re.search(
        r"kind:\s*(Pod|Deployment|StatefulSet|DaemonSet|Job|CronJob)", text
    ):
        found.add("privilege_escalation")
    if re.search(r"SYS_ADMIN|NET_ADMIN|-\s*ALL\b", text):
        found.add("dangerous_capabilities")
    if "limits:" not in text and "containers:" in text:
        found.add("no_limits")
    if "requests:" not in text and "containers:" in text:
        found.add("no_requests")
    if re.search(r"image:\s*\S+:latest\b", text) or re.search(r"image:\s*[^\s:@]+\s*$", text, re.M):
        found.add("unpinned_image")
    if "hostPath" in text:
        found.add("host_path")
    if re.search(r"host(Network|PID|IPC):\s*true", text):
        found.add("host_namespaces")
    if "Probe" not in text and "containers:" in text:
        found.add("no_probes")
    if re.search(r"""verbs:.*["']\*["']|resources:.*["']\*["']""", text):
        found.add("rbac_wildcard")
    return found
