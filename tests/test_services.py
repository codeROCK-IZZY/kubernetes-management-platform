"""Claude patches (real SDK, canned responses) checked by the policy engine, and the dashboard."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import anthropic
import httpx2
from fastapi.testclient import TestClient

from kubefleet.app import create_app
from kubefleet.fixes import FALLBACK_BETA, MODEL, ClaudeFixer
from kubefleet.fleet import demo

POD = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {"name": "web"},
    "spec": {
        "template": {
            "spec": {
                "containers": [
                    {
                        "name": "web",
                        "image": "r/web:latest",
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "allowPrivilegeEscalation": False,
                        },
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "64Mi"},
                            "limits": {"cpu": "200m", "memory": "128Mi"},
                        },
                    }
                ]
            }
        }
    },
}


def message(*content: dict[str, Any], stop: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": list(content),
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class Script:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses: Iterator[dict[str, Any]] = iter(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx2.Response(200, json=next(self.responses))


def fixer(script: Script) -> ClaudeFixer:
    client = anthropic.Anthropic(
        api_key="test",
        base_url="http://api.test",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(script)),
    )
    return ClaudeFixer(client)


def patch(*ops: tuple[str, str, Any]) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": "t",
        "name": "propose_patch",
        "input": {
            "ops": [{"op": o, "path": p, "value_json": json.dumps(v)} for o, p, v in ops],
            "explanation": "pin the image and add probes",
        },
    }


C = "/spec/template/spec/containers/0"


def test_a_patch_that_fixes_the_violations_is_accepted() -> None:
    script = Script(
        [
            message(
                patch(
                    ("replace", f"{C}/image", "r/web:3.4.1"),
                    ("add", f"{C}/readinessProbe", {"httpGet": {"path": "/ready", "port": 8080}}),
                )
            )
        ]
    )
    p = fixer(script).propose(POD, ["unpinned_image", "no_probes"])
    assert p.accepted and p.result is not None and p.result.fixed == {"unpinned_image", "no_probes"}
    req = script.requests[0]
    assert req["model"] == MODEL and req["tools"][0]["strict"] is True
    assert req["output_config"] == {"effort": "low"} and "thinking" not in req and "tool_choice" not in req
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"} and "<manifest>" in req["messages"][0]["content"]
    assert FALLBACK_BETA in script.headers[0]["anthropic-beta"]


def test_patches_outside_the_allowed_fields_or_that_break_things_are_rejected() -> None:
    sneaky = fixer(Script([message(patch(("replace", "/metadata/name", "other")))])).propose(POD, ["unpinned_image"])
    assert not sneaky.accepted and "may not change" in sneaky.problems[0]
    worse = fixer(
        Script(
            [message(patch(("replace", f"{C}/image", "r/web:3.4.1"), ("add", f"{C}/securityContext/privileged", True)))]
        )
    ).propose(POD, ["unpinned_image"])
    assert not worse.accepted and any("introduces privileged" in x for x in worse.problems)
    half = fixer(Script([message(patch(("add", f"{C}/livenessProbe", {"tcpSocket": {"port": 8080}})))])).propose(
        POD, ["unpinned_image", "no_probes"]
    )
    assert not half.accepted and "unpinned_image is still violated" in half.problems


def test_refusal() -> None:
    p = fixer(Script([message({"type": "text", "text": "No."}, stop="refusal")])).propose(POD, ["no_probes"])
    assert not p.accepted and p.problems == ["no patch was proposed"]


def test_dashboard_and_api() -> None:
    c = TestClient(create_app(demo()))
    h = c.get("/healthz").json()
    assert h["clusters"] == 3 and h["objects"] > 50
    for path in ("/", "/workloads", "/upgrades"):
        assert c.get(path).status_code == 200
    slug = c.get("/workloads").text.split("href='/workloads/")[1].split("'")[0]
    assert c.get(f"/workloads/{slug}").status_code == 200
    assert c.get("/workloads/nope").status_code == 404
    clusters = c.get("/api/clusters").json()
    assert any(cl["upgrade_blockers"] for cl in clusters)
    found = c.post(
        "/api/check", json={"yaml": "kind: Pod\nmetadata: {name: x}\nspec:\n  containers: [{name: a, image: nginx}]"}
    ).json()
    assert {v["rule"] for v in found["violations"]} >= {"run_as_root", "unpinned_image"}
    assert c.post("/api/check", json={"yaml": "a: [unclosed"}).status_code == 422
