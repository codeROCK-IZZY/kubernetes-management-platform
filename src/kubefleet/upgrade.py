"""Upgrade readiness: objects using API versions that a target Kubernetes version no longer serves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# (apiVersion, kind) -> (removed in minor version, replacement apiVersion)
REMOVED: dict[tuple[str, str], tuple[int, str]] = {
    ("extensions/v1beta1", "Deployment"): (16, "apps/v1"),
    ("apps/v1beta1", "Deployment"): (16, "apps/v1"),
    ("apps/v1beta2", "Deployment"): (16, "apps/v1"),
    ("extensions/v1beta1", "DaemonSet"): (16, "apps/v1"),
    ("apps/v1beta2", "StatefulSet"): (16, "apps/v1"),
    ("extensions/v1beta1", "Ingress"): (22, "networking.k8s.io/v1"),
    ("networking.k8s.io/v1beta1", "Ingress"): (22, "networking.k8s.io/v1"),
    ("rbac.authorization.k8s.io/v1beta1", "Role"): (22, "rbac.authorization.k8s.io/v1"),
    ("rbac.authorization.k8s.io/v1beta1", "ClusterRole"): (22, "rbac.authorization.k8s.io/v1"),
    ("batch/v1beta1", "CronJob"): (25, "batch/v1"),
    ("policy/v1beta1", "PodDisruptionBudget"): (25, "policy/v1"),
    ("policy/v1beta1", "PodSecurityPolicy"): (25, "Pod Security Admission (no replacement object)"),
    ("autoscaling/v2beta1", "HorizontalPodAutoscaler"): (25, "autoscaling/v2"),
    ("discovery.k8s.io/v1beta1", "EndpointSlice"): (25, "discovery.k8s.io/v1"),
    ("autoscaling/v2beta2", "HorizontalPodAutoscaler"): (26, "autoscaling/v2"),
    ("storage.k8s.io/v1beta1", "CSIStorageCapacity"): (27, "storage.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta2", "FlowSchema"): (29, "flowcontrol.apiserver.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta3", "FlowSchema"): (32, "flowcontrol.apiserver.k8s.io/v1"),
}


@dataclass(frozen=True)
class Blocker:
    obj: str
    api_version: str
    removed_in: str
    use_instead: str


def minor(version: str) -> int:
    return int(version.split(".")[1])


def blockers(objects: list[dict[str, Any]], current: str, target: str) -> list[Blocker]:
    """Objects that would stop working on the way from `current` to `target` (e.g. "1.24" to "1.26")."""
    out = []
    for o in objects:
        key = (str(o.get("apiVersion")), str(o.get("kind")))
        if key in REMOVED:
            removed, instead = REMOVED[key]
            if minor(current) < removed <= minor(target):  # already-removed APIs could not be running here
                name = f"{key[1]}/{(o.get('metadata') or {}).get('name', '?')}"
                out.append(Blocker(name, key[0], f"1.{removed}", instead))
    return out


def path(current: str, target: str) -> list[str]:
    """Kubernetes control planes upgrade one minor version at a time."""
    return [f"1.{m}" for m in range(minor(current) + 1, minor(target) + 1)]
