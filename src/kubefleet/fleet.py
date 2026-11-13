"""A demo fleet: clusters on different Kubernetes versions, their workloads, usage and leftover objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .fixes import FixResult, autofix
from .manifests import generate
from .policy import Violation, check_object
from .rightsize import HELD_BACK_SHAPES, TUNING_SHAPES, Recommendation, Workload, current, judge, recommend, workload
from .upgrade import REMOVED, Blocker, blockers, minor


@dataclass
class Item:
    cluster: str
    obj: dict[str, Any]
    violations: list[Violation]
    usage: Workload | None = None
    recommendation: Recommendation | None = None
    fix: FixResult | None = None

    @property
    def name(self) -> str:
        return f"{self.obj.get('kind')}/{(self.obj.get('metadata') or {}).get('name')}"

    @property
    def slug(self) -> str:
        return f"{self.cluster}--{self.obj.get('kind', '').lower()}--{(self.obj.get('metadata') or {}).get('name')}"


@dataclass
class Cluster:
    name: str
    version: str
    target: str
    items: list[Item] = field(default_factory=list)

    @property
    def blockers(self) -> list[Blocker]:
        return blockers([i.obj for i in self.items], self.version, self.target)


LEFTOVERS = [
    {
        "apiVersion": "batch/v1beta1",
        "kind": "CronJob",
        "metadata": {"name": "cleanup"},
        "spec": {
            "schedule": "0 3 * * *",
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "containers": [
                                {
                                    "name": "cleanup",
                                    "image": "registry.example.com/ops/cleanup:1.4.0",
                                    "securityContext": {
                                        "runAsNonRoot": True,
                                        "runAsUser": 10001,
                                        "allowPrivilegeEscalation": False,
                                    },
                                    "resources": {
                                        "requests": {"cpu": "50m", "memory": "64Mi"},
                                        "limits": {"cpu": "200m", "memory": "128Mi"},
                                    },
                                }
                            ],
                        }
                    }
                }
            },
        },
    },
    {
        "apiVersion": "policy/v1beta1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": "checkout"},
        "spec": {"minAvailable": 1, "selector": {"matchLabels": {"app": "checkout"}}},
    },
    {
        "apiVersion": "autoscaling/v2beta2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {"name": "search"},
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "search"},
            "minReplicas": 2,
            "maxReplicas": 10,
        },
    },
    {
        "apiVersion": "flowcontrol.apiserver.k8s.io/v1beta2",
        "kind": "FlowSchema",
        "metadata": {"name": "batch"},
        "spec": {"priorityLevelConfiguration": {"name": "workload-low"}, "matchingPrecedence": 9000},
    },
]


def demo() -> list[Cluster]:
    clusters = [
        Cluster("prod-us", "1.24", "1.26"),
        Cluster("prod-eu", "1.27", "1.29"),
        Cluster("staging", "1.28", "1.30"),
    ]
    for k, cl in enumerate(clusters):
        manifests = generate(14, 300 + k, "heldout") + generate(6, 400 + k, "fresh")
        for j, m in enumerate(manifests):
            for doc in yaml.safe_load_all(m.text):
                if not isinstance(doc, dict):
                    continue
                item = Item(cl.name, doc, check_object(doc))
                if doc.get("kind") in ("Deployment", "StatefulSet", "DaemonSet"):
                    shapes = HELD_BACK_SHAPES if j % 5 == 0 else TUNING_SHAPES
                    item.usage = workload(5000 + 100 * k + j, shapes)
                    item.recommendation = recommend(item.usage)
                cpu = f"{max(int(item.recommendation.cpu_request * 1000), 10)}m" if item.recommendation else "250m"
                mem = f"{max(int(item.recommendation.memory), 64)}Mi" if item.recommendation else "256Mi"
                item.fix = autofix(doc, cpu, mem) if item.violations else None
                cl.items.append(item)
        for o in LEFTOVERS:  # only objects whose API the cluster still serves today
            if REMOVED[(str(o["apiVersion"]), str(o["kind"]))][0] > minor(cl.version):
                cl.items.append(Item(cl.name, o, check_object(o)))
    return clusters


def savings(c: Cluster) -> dict[str, float]:
    cur_cpu = rec_cpu = cur_mem = rec_mem = 0.0
    for i in c.items:
        if i.usage is None or i.recommendation is None:
            continue
        cur, rec = judge(i.usage, current(i.usage)), judge(i.usage, i.recommendation)
        cur_cpu += cur["cpu_requested"]
        rec_cpu += rec["cpu_requested"]
        cur_mem += cur["memory_requested"]
        rec_mem += rec["memory_requested"]
    return {"cpu_now": cur_cpu, "cpu_recommended": rec_cpu, "memory_now": cur_mem, "memory_recommended": rec_mem}
