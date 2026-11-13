"""Policies, fixes, rightsizing and upgrades."""

from __future__ import annotations

import numpy as np
import yaml

from kubefleet.fixes import AUTO, apply_patch, autofix
from kubefleet.manifests import generate
from kubefleet.policy import check, check_object, image_pinned, naive, pod_spec
from kubefleet.rightsize import DAY, HELD_BACK_SHAPES, TUNING_SHAPES, Workload, judge, recommend, workload
from kubefleet.rightsize import naive as naive_rs
from kubefleet.upgrade import blockers, path

GOOD = {"runAsNonRoot": True, "runAsUser": 10001, "allowPrivilegeEscalation": False}
RES = {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}}


def pod(**container: object) -> dict[str, object]:
    c = {
        "name": "app",
        "image": "r.example.com/app:1.0",
        "securityContext": dict(GOOD),
        "resources": RES,
        "readinessProbe": {"httpGet": {"path": "/", "port": 80}},
    }
    c.update(container)
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [c]}}


def rules(obj: dict[str, object]) -> set[str]:
    return {v.rule for v in check_object(obj)}


def test_a_compliant_pod_passes() -> None:
    assert rules(pod()) == set()


def test_images() -> None:
    assert image_pinned("r.example.com/app:1.2.3")
    assert image_pinned("r.example.com:5000/app@sha256:" + "a" * 64)
    assert not image_pinned("r.example.com:5000/app")  # a port is not a tag
    assert not image_pinned("app:latest")
    assert not image_pinned("app")


def test_security_context_inheritance() -> None:
    p = pod(securityContext={"allowPrivilegeEscalation": False})
    assert "run_as_root" in rules(p)
    p["spec"]["securityContext"] = {"runAsNonRoot": True, "runAsUser": 1000}  # type: ignore[index]
    assert "run_as_root" not in rules(p)
    p["spec"]["containers"][0]["securityContext"]["runAsNonRoot"] = False  # type: ignore[index]
    assert "run_as_root" in rules(p)  # the container overrides the pod


def test_cronjob_is_unwrapped_and_needs_no_probes() -> None:
    c = {"name": "j", "image": "r/j:1", "securityContext": dict(GOOD, privileged=True), "resources": RES}
    cj = {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": "c"},
        "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [c]}}}}},
    }
    assert pod_spec(cj) is not None
    assert rules(cj) == {"privileged"}


def test_init_and_ephemeral_containers_are_checked() -> None:
    p = pod()
    p["spec"]["initContainers"] = [{"name": "i", "image": "r/i:latest", "securityContext": dict(GOOD)}]  # type: ignore[index]
    p["spec"]["ephemeralContainers"] = [
        {"name": "d", "image": "r/d:1", "securityContext": {**GOOD, "privileged": True}}
    ]  # type: ignore[index]
    assert rules(p) == {"unpinned_image", "no_limits", "no_requests", "privileged"}


def test_multi_document_and_rbac() -> None:
    text = (
        yaml.safe_dump(pod())
        + "---\n"
        + yaml.safe_dump(
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": "r"},
                "rules": [{"apiGroups": [""], "resources": ["*"], "verbs": ["get"]}],
            }
        )
    )
    assert {v.rule for v in check(text)} == {"rbac_wildcard"}


def test_engine_beats_text_search_on_fresh_forms() -> None:
    ms = generate(45, 9, "fresh")
    engine = sum(len({v.rule for v in check(m.text)} ^ m.truth) for m in ms)
    shortcut = sum(len(naive(m.text) ^ m.truth) for m in ms)
    assert engine == 0 and shortcut > 20


def test_autofix_is_verified_and_leaves_decisions_to_people() -> None:
    bad = pod(
        securityContext={"privileged": True, "runAsUser": 0, "capabilities": {"add": ["SYS_ADMIN"]}},
        image="r/app:latest",
    )
    bad["spec"]["containers"][0].pop("resources")  # type: ignore[index]
    r = autofix(bad)
    assert (
        r.fixed
        == {"privileged", "run_as_root", "privilege_escalation", "dangerous_capabilities", "no_limits", "no_requests"}
        and r.fixed <= AUTO
    )
    assert r.remaining == {"unpinned_image"} and not r.introduced
    assert "privileged" in bad["spec"]["containers"][0]["securityContext"]  # type: ignore[index]  # original untouched


def test_json_patch() -> None:
    out = apply_patch(
        pod(),
        [
            {"op": "replace", "path": "/spec/containers/0/image", "value": "r/app:2.0"},
            {"op": "remove", "path": "/spec/containers/0/readinessProbe"},
        ],
    )
    c = out["spec"]["containers"][0]
    assert c["image"] == "r/app:2.0" and "readinessProbe" not in c


def test_rightsizing_respects_memory_growth() -> None:
    # nightly batch bursts get "average x 1.2" killed; the recommendation must not be
    batch = next(
        w
        for w in (workload(s, HELD_BACK_SHAPES) for s in range(1, 200))
        if w.shape == "batch" and judge(w, naive_rs(w))["oom"]
    )
    assert judge(batch, recommend(batch))["oom"] == 0
    # a clearly leaking container: growth over the coming two weeks must be included (the evaluation's simulated
    # leak turned out too slow to test this; see the known limitations)
    n = 28 * DAY
    leak = Workload("leak", "leak", np.full(n, 0.2), 300 + np.arange(n) * 0.05, 1.0, 2000.0)
    assert judge(leak, recommend(leak))["oom"] == 0
    assert judge(leak, naive_rs(leak))["oom"] == 1
    steady = workload(3, TUNING_SHAPES)
    r = judge(steady, recommend(steady))
    assert r["cpu_requested"] < steady.current_cpu and r["contention"] < 0.05


def test_upgrade_blockers_and_path() -> None:
    objs = [
        {"apiVersion": "batch/v1beta1", "kind": "CronJob", "metadata": {"name": "c"}},
        {"apiVersion": "autoscaling/v2beta2", "kind": "HorizontalPodAutoscaler", "metadata": {"name": "h"}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "d"}},
    ]
    assert [b.obj for b in blockers(objs, "1.24", "1.25")] == ["CronJob/c"]
    assert [b.obj for b in blockers(objs, "1.24", "1.26")] == ["CronJob/c", "HorizontalPodAutoscaler/h"]
    assert blockers(objs, "1.26", "1.28") == []  # already removed APIs cannot be running there
    assert path("1.24", "1.27") == ["1.25", "1.26", "1.27"]
