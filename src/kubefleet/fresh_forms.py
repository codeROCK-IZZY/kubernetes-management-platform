"""Manifest forms written after the policy engine was frozen: the test of whether it generalises.

Each case is built with its violations known in advance, following Kubernetes semantics: probes are not required for
Jobs and CronJobs, ephemeral containers cannot set resources or probes, a container's securityContext overrides the
pod's, a digest pins an image, a registry port is not a tag, and comments are not configuration.
"""

from __future__ import annotations

import json
import random
from typing import Any

import yaml

from .manifests import Manifest

GOOD_SC = {
    "runAsNonRoot": True,
    "runAsUser": 10001,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
}
RES = {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}}
PROBES = {
    "readinessProbe": {"httpGet": {"path": "/ready", "port": 8080}},
    "livenessProbe": {"httpGet": {"path": "/healthz", "port": 8080}},
}


def _c(name: str, image: str, **extra: Any) -> dict[str, Any]:
    c: dict[str, Any] = {"name": name, "image": image, "securityContext": dict(GOOD_SC), "resources": RES}
    c.update(extra)
    return c


def cronjob(rng: random.Random, i: int) -> Manifest:
    bad = set()
    c = _c("report", f"registry.example.com/jobs/report:2.{i % 9}.0")
    if rng.random() < 0.5:
        c["securityContext"] = {**GOOD_SC, "privileged": True}
        bad.add("privileged")
    obj = {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": f"nightly-{i}"},
        "spec": {
            "schedule": "0 2 * * *",
            "jobTemplate": {"spec": {"template": {"spec": {"restartPolicy": "OnFailure", "containers": [c]}}}},
        },
    }
    return Manifest(f"nightly-{i}", yaml.safe_dump(obj, sort_keys=False), bad, "fresh")  # no probes needed


def job_with_init(rng: random.Random, i: int) -> Manifest:
    bad = set()
    init = _c("migrate", f"registry.example.com/db/migrate:1.{i % 7}")
    if rng.random() < 0.5:
        init.pop("resources")
        bad |= {"no_limits", "no_requests"}
    if rng.random() < 0.5:
        init["image"] = "registry.example.com/db/migrate:latest"
        bad.add("unpinned_image")
    obj = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": f"migrate-{i}"},
        "spec": {
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "initContainers": [init],
                    "containers": [_c("main", f"registry.example.com/db/run:3.{i % 5}")],
                }
            }
        },
    }
    return Manifest(f"migrate-{i}", yaml.safe_dump(obj, sort_keys=False), bad, "fresh")


def override(rng: random.Random, i: int) -> Manifest:
    """Pod-level runAsNonRoot: true, but a container overrides it."""
    bad = set()
    c = _c("api", f"registry.example.com/api:4.{i % 9}.1", **PROBES)
    c["securityContext"] = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    if rng.random() < 0.5:
        c["securityContext"]["runAsNonRoot"] = False
        bad.add("run_as_root")
    obj = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": f"api-{i}"},
        "spec": {
            "selector": {"matchLabels": {"app": "api"}},
            "template": {
                "metadata": {"labels": {"app": "api"}},
                "spec": {"securityContext": {"runAsNonRoot": True, "runAsUser": 10001}, "containers": [c]},
            },
        },
    }
    return Manifest(f"api-{i}", yaml.safe_dump(obj, sort_keys=False), bad, "fresh")


def images(rng: random.Random, i: int) -> Manifest:
    """Digests pin an image; a registry port is not a tag."""
    choice = rng.choice(["digest", "port_no_tag", "port_with_tag"])
    image = {
        "digest": "registry.example.com:5000/web@sha256:" + "ab" * 32,
        "port_no_tag": "registry.example.com:5000/web",
        "port_with_tag": "registry.example.com:5000/web:5.1.0",
    }[choice]
    bad = {"unpinned_image"} if choice == "port_no_tag" else set()
    obj = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": f"web-{i}"},
        "spec": {
            "selector": {"matchLabels": {"app": "web"}},
            "template": {"metadata": {"labels": {"app": "web"}}, "spec": {"containers": [_c("web", image, **PROBES)]}},
        },
    }
    return Manifest(f"web-{i}", yaml.safe_dump(obj, sort_keys=False), bad, "fresh")


def multi_doc(rng: random.Random, i: int) -> Manifest:
    """A Service, a Deployment and a Role in one file, separated by ---."""
    bad = set()
    dep: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": f"shop-{i}"},
        "spec": {
            "selector": {"matchLabels": {"app": "shop"}},
            "template": {
                "metadata": {"labels": {"app": "shop"}},
                "spec": {"containers": [_c("shop", f"registry.example.com/shop:1.{i % 4}", **PROBES)]},
            },
        },
    }
    if rng.random() < 0.5:
        dep["spec"]["template"]["spec"]["hostNetwork"] = True
        bad.add("host_namespaces")
    role: dict[str, Any] = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": f"shop-{i}"},
        "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}],
    }
    if rng.random() < 0.4:
        role["rules"][0]["verbs"] = ["*"]
        bad.add("rbac_wildcard")
    svc: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": f"shop-{i}"},
        "spec": {"selector": {"app": "shop"}, "ports": [{"port": 80, "targetPort": 8080}]},
    }
    text = "---\n".join(yaml.safe_dump(o, sort_keys=False) for o in (svc, dep, role))
    return Manifest(f"shop-{i}", text, bad, "fresh")


def anchors(rng: random.Random, i: int) -> Manifest:
    """Container defaults shared through a YAML anchor and merge keys."""
    priv = rng.random() < 0.5
    text = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: pair-{i}
spec:
  selector:
    matchLabels: {{app: pair}}
  template:
    metadata:
      labels: {{app: pair}}
    spec:
      containers:
        - &base
          name: one
          image: registry.example.com/pair/one:1.0.{i % 9}
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            allowPrivilegeEscalation: false
            capabilities: {{drop: [ALL]}}
          resources:
            requests: {{cpu: 100m, memory: 128Mi}}
            limits: {{cpu: 500m, memory: 512Mi}}
          readinessProbe: {{httpGet: {{path: /ready, port: 8080}}}}
        - <<: *base
          name: two
          image: registry.example.com/pair/two:1.0.{i % 9}
"""
    bad: set[str] = set()
    if priv:
        text += """          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            allowPrivilegeEscalation: false
            privileged: true
"""
        bad.add("privileged")
    return Manifest(f"pair-{i}", text, bad, "fresh")


def json_manifest(rng: random.Random, i: int) -> Manifest:
    """kubectl accepts JSON too."""
    c = _c("worker", f"registry.example.com/worker:2.{i % 6}", **PROBES)
    bad = set()
    if rng.random() < 0.5:
        c["securityContext"] = {**GOOD_SC, "capabilities": {"add": ["NET_ADMIN"], "drop": ["ALL"]}}
        bad.add("dangerous_capabilities")
    obj = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": f"worker-{i}"},
        "spec": {
            "selector": {"matchLabels": {"app": "worker"}},
            "template": {"metadata": {"labels": {"app": "worker"}}, "spec": {"containers": [c]}},
        },
    }
    return Manifest(f"worker-{i}", json.dumps(obj, indent=2), bad, "fresh")


def daemonset(rng: random.Random, i: int) -> Manifest:
    """A node agent: hostPath is real here, and the comment is not configuration."""
    agent = _c("agent", f"registry.example.com/agent:7.{i % 3}", **PROBES)
    bad = set()
    spec: dict[str, Any] = {"containers": [agent]}
    if rng.random() < 0.6:
        spec["volumes"] = [{"name": "logs", "hostPath": {"path": "/var/log"}}]
        agent["volumeMounts"] = [{"name": "logs", "mountPath": "/host/logs", "readOnly": True}]
        bad.add("host_path")
    obj = {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {"name": f"agent-{i}"},
        "spec": {
            "selector": {"matchLabels": {"app": "agent"}},
            "template": {"metadata": {"labels": {"app": "agent"}}, "spec": spec},
        },
    }
    text = "# Never set privileged: true or hostNetwork: true on this agent.\n" + yaml.safe_dump(obj, sort_keys=False)
    return Manifest(f"agent-{i}", text, bad, "fresh")


def ephemeral(rng: random.Random, i: int) -> Manifest:
    """A Pod with a debug (ephemeral) container: no resources or probes allowed, but security still applies."""
    debug: dict[str, Any] = {
        "name": "debug",
        "image": "registry.example.com/tools/debug:1.2",
        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
    }
    bad = set()
    if rng.random() < 0.5:
        debug["securityContext"] = {"privileged": True, "runAsNonRoot": True, "runAsUser": 10001}
        bad.add("privileged")
    obj = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": f"box-{i}"},
        "spec": {
            "containers": [_c("box", f"registry.example.com/box:1.{i % 5}", **PROBES)],
            "ephemeralContainers": [debug],
        },
    }
    return Manifest(f"box-{i}", yaml.safe_dump(obj, sort_keys=False), bad, "fresh")


FORMS = [cronjob, job_with_init, override, images, multi_doc, anchors, json_manifest, daemonset, ephemeral]


def generate_fresh(n: int, seed: int) -> list[Manifest]:
    rng = random.Random(seed)
    return [FORMS[i % len(FORMS)](rng, i) for i in range(n)]
