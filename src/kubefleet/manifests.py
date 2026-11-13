"""Generated Kubernetes manifests with known policy violations.

Each manifest is built from a chosen set of violations, so the truth comes from the generator, not from the engine
being tested. Forms:
- `dev`: Deployments, StatefulSets, Pods and Roles; one or two containers, where a violation may sit in only one of
  them; security settings at pod or container level. The policy engine was written against these.
- `heldout`: the same forms, new seeds.
- `fresh`: forms added after the engine was frozen (see `fresh_forms`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import yaml

POD_RULES = [
    "privileged",
    "run_as_root",
    "privilege_escalation",
    "dangerous_capabilities",
    "no_limits",
    "no_requests",
    "unpinned_image",
    "host_path",
    "host_namespaces",
    "no_probes",
]


@dataclass
class Manifest:
    name: str
    text: str
    truth: set[str] = field(default_factory=set)
    form: str = "dev"


def _container(rng: random.Random, name: str, bad: set[str], pod_level_nonroot: bool, probes: bool) -> dict[str, Any]:
    tag = "latest" if "unpinned_image" in bad and rng.random() < 0.5 else None
    image = f"registry.example.com/team/{name}" + (
        "" if "unpinned_image" in bad and tag is None else f":{tag or f'1.{rng.randint(0, 9)}.{rng.randint(0, 20)}'}"
    )
    c: dict[str, Any] = {"name": name, "image": image, "ports": [{"containerPort": 8080}]}
    sc: dict[str, Any] = {}
    if "privileged" in bad:
        sc["privileged"] = True
    if "run_as_root" in bad:
        sc["runAsUser"] = 0
    elif not pod_level_nonroot:
        sc["runAsNonRoot"] = True
        sc["runAsUser"] = 10001
    sc["allowPrivilegeEscalation"] = "privilege_escalation" not in bad and False
    if "privilege_escalation" in bad:
        sc.pop("allowPrivilegeEscalation")
    if "dangerous_capabilities" in bad:
        sc["capabilities"] = {"add": [rng.choice(["SYS_ADMIN", "NET_ADMIN"])], "drop": ["ALL"]}
    else:
        sc["capabilities"] = {"drop": ["ALL"]}
    c["securityContext"] = sc
    res: dict[str, Any] = {}
    if "no_requests" not in bad:
        res["requests"] = {"cpu": f"{rng.choice([100, 250, 500])}m", "memory": f"{rng.choice([128, 256, 512])}Mi"}
    if "no_limits" not in bad:
        res["limits"] = {"cpu": f"{rng.choice([500, 1000])}m", "memory": f"{rng.choice([512, 1024])}Mi"}
    if res:
        c["resources"] = res
    if probes and "no_probes" not in bad:
        c["readinessProbe"] = {"httpGet": {"path": "/ready", "port": 8080}}
        c["livenessProbe"] = {"httpGet": {"path": "/healthz", "port": 8080}}
    return c


def _workload(rng: random.Random, i: int, bad: set[str]) -> dict[str, Any]:
    kind = rng.choice(["Deployment", "Deployment", "StatefulSet", "Pod"])
    name = f"svc-{i}"
    pod_level = rng.random() < 0.5
    n = rng.choice([1, 1, 2])
    # a container-level violation lands in one container only
    containers = []
    target = rng.randrange(n)
    for k in range(n):
        own = {r for r in bad if r not in ("host_path", "host_namespaces")} if k == target else set()
        containers.append(_container(rng, f"{name}-{k}" if n > 1 else name, own, pod_level, True))
    spec: dict[str, Any] = {"containers": containers}
    if pod_level:
        spec["securityContext"] = {"runAsNonRoot": True, "runAsUser": 10001}
    if "host_namespaces" in bad:
        spec[rng.choice(["hostNetwork", "hostPID"])] = True
    if "host_path" in bad:
        spec["volumes"] = [{"name": "host", "hostPath": {"path": "/var/run"}}]
        containers[0]["volumeMounts"] = [{"name": "host", "mountPath": "/host"}]
    meta = {"name": name, "labels": {"app": name}}
    if kind == "Pod":
        return {"apiVersion": "v1", "kind": "Pod", "metadata": meta, "spec": spec}
    return {
        "apiVersion": "apps/v1",
        "kind": kind,
        "metadata": meta,
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name}}, "spec": spec},
        },
    }


def _role(rng: random.Random, i: int, bad: set[str]) -> dict[str, Any]:
    wildcard = "rbac_wildcard" in bad
    rules = [
        {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "list"]},
        {
            "apiGroups": ["apps"],
            "resources": ["*"] if wildcard and rng.random() < 0.5 else ["deployments"],
            "verbs": ["*"] if wildcard and rng.random() >= 0.5 else ["get", "patch"],
        },
    ]
    if wildcard and rules[1]["resources"] != ["*"]:
        rules[1]["verbs"] = ["*"]
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": rng.choice(["Role", "ClusterRole"]),
        "metadata": {"name": f"role-{i}"},
        "rules": rules,
    }


def generate(n: int, seed: int, form: str = "dev") -> list[Manifest]:
    if form == "fresh":
        from .fresh_forms import generate_fresh

        return generate_fresh(n, seed)
    rng = random.Random(seed)
    out = []
    for i in range(n):
        if rng.random() < 0.12:
            bad = {"rbac_wildcard"} if rng.random() < 0.5 else set()
            obj = _role(rng, i, bad)
        else:
            bad = {r for r in POD_RULES if rng.random() < 0.18}
            obj = _workload(rng, i, bad)
            if obj["kind"] not in ("Deployment", "StatefulSet", "Pod"):
                bad.discard("no_probes")
        out.append(Manifest(obj["metadata"]["name"], yaml.safe_dump(obj, sort_keys=False), bad, form))
    return out
