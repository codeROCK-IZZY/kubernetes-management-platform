"""Fixing violations, and proving each fix by checking the result again.

`autofix` handles the rules with one safe answer: drop `privileged`, set `allowPrivilegeEscalation: false`, run as a
non-root user, remove dangerous capabilities, and set requests and limits (from the rightsizing recommendation when
there is one). Image tags, hostPath volumes, host namespaces, probes and RBAC need a decision about the application,
so they are left for a person, or for Claude to propose.

`ClaudeFixer` asks `claude-opus-5-5` (low effort, refusal fallbacks) for a JSON patch through a strict tool. The patch
is applied to a copy and accepted only if: every operation touches an allowed field (container images, security
contexts, resources, probes; pod volumes and host namespaces), the targeted violation is gone, and no new violation
appears. Every fix, automatic or not, is verified the same way.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic
import yaml
from anthropic.types.beta import BetaMessage, BetaOutputConfigParam, BetaTextBlockParam, BetaToolParam

from .policy import DANGEROUS_CAPS, RULES, check_object, pod_spec

AUTO = {"privileged", "privilege_escalation", "run_as_root", "dangerous_capabilities", "no_limits", "no_requests"}
MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class FixResult:
    fixed: set[str] = field(default_factory=set)  # rules that were violated before and are not after
    remaining: set[str] = field(default_factory=set)
    introduced: set[str] = field(default_factory=set)
    obj: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return not self.introduced


def _rules(obj: dict[str, Any]) -> set[str]:
    return {v.rule for v in check_object(obj)}


def verify(before: dict[str, Any], after: dict[str, Any]) -> FixResult:
    b, a = _rules(before), _rules(after)
    return FixResult(b - a, a & b, a - b, after)


def autofix(obj: dict[str, Any], cpu: str = "250m", memory: str = "256Mi") -> FixResult:
    fixed = copy.deepcopy(obj)
    spec = pod_spec(fixed)
    if spec is not None:
        for group in ("containers", "initContainers", "ephemeralContainers"):
            for c in spec.get(group) or []:
                sc = c.setdefault("securityContext", {})
                sc.pop("privileged", None)
                sc["allowPrivilegeEscalation"] = False
                sc["runAsNonRoot"] = True
                if sc.get("runAsUser") in (0, "0", None):
                    sc["runAsUser"] = 10001
                caps = sc.get("capabilities") or {}
                if caps.get("add"):
                    caps["add"] = [x for x in caps["add"] if str(x).upper() not in DANGEROUS_CAPS]
                    if not caps["add"]:
                        caps.pop("add")
                if group == "ephemeralContainers":
                    continue
                res = c.setdefault("resources", {})
                res.setdefault("requests", {}).setdefault("cpu", cpu)
                res["requests"].setdefault("memory", memory)
                res.setdefault("limits", {}).setdefault("cpu", cpu)
                res["limits"].setdefault("memory", memory)
    return verify(obj, fixed)


ALLOWED = re.compile(
    r"^/spec/(template/spec/|jobTemplate/spec/template/spec/)?("
    r"(containers|initContainers)/\d+/(image|securityContext(/.*)?|resources(/.*)?|readinessProbe(/.*)?"
    r"|livenessProbe(/.*)?)|volumes(/.*)?|hostNetwork|hostPID|hostIPC)$"
)


def apply_patch(obj: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """A small JSON Patch (RFC 6902): add, replace and remove."""
    out = copy.deepcopy(obj)
    for op in ops:
        parts = [p.replace("~1", "/").replace("~0", "~") for p in str(op["path"]).lstrip("/").split("/")]
        node: Any = out
        for p in parts[:-1]:
            node = node[int(p)] if isinstance(node, list) else node.setdefault(p, {})
        last = parts[-1]
        if op["op"] == "remove":
            if isinstance(node, list):
                node.pop(int(last))
            else:
                node.pop(last, None)
        elif isinstance(node, list):
            if last == "-":
                node.append(op.get("value"))
            else:
                node[int(last)] = op.get("value")
        else:
            node[last] = op.get("value")
    return out


PATCH: BetaToolParam = {
    "name": "propose_patch",
    "description": "Propose a JSON Patch that fixes the listed violations without changing anything else.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ops", "explanation"],
        "properties": {
            "ops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op", "path", "value_json"],
                    "properties": {
                        "op": {"type": "string", "enum": ["add", "replace", "remove"]},
                        "path": {"type": "string"},
                        "value_json": {"type": "string", "description": 'The value as JSON; "null" for remove.'},
                    },
                },
            },
            "explanation": {"type": "string"},
        },
    },
}


@dataclass
class Proposal:
    accepted: bool
    result: FixResult | None
    explanation: str
    problems: list[str] = field(default_factory=list)


class ClaudeFixer:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self.client = client if client is not None else anthropic.Anthropic(max_retries=2)

    def _create(self, obj: dict[str, Any], rules: list[str]) -> BetaMessage:
        system: list[BetaTextBlockParam] = [
            {
                "type": "text",
                "cache_control": {"type": "ephemeral"},
                "text": (
                    "You fix Kubernetes manifests against admission policies. Propose the smallest JSON Patch that "
                    "fixes the listed violations; change nothing else. Call propose_patch once."
                ),
            }
        ]
        config: BetaOutputConfigParam = {"effort": "low"}
        problems = "\n".join(f"- {r}: {RULES[r][1]}. {RULES[r][2]}" for r in rules)
        body = f"Violations:\n{problems}\n\nManifest:\n<manifest>\n{yaml.safe_dump(obj, sort_keys=False)}</manifest>"
        return self.client.beta.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[PATCH],
            output_config=config,
            messages=[{"role": "user", "content": body}],
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def propose(self, obj: dict[str, Any], rules: list[str]) -> Proposal:
        resp = self._create(obj, rules)
        use = next((b for b in resp.content if b.type == "tool_use" and b.name == "propose_patch"), None)
        if resp.stop_reason == "refusal" or use is None:
            return Proposal(False, None, "", ["no patch was proposed"])
        data = dict(use.input)
        got = data.get("ops")
        raw_ops: list[object] = list(got) if isinstance(got, list) else []
        ops: list[dict[str, Any]] = []
        problems: list[str] = []
        for op in raw_ops:
            if not isinstance(op, dict):
                continue
            p = str(op.get("path", ""))
            if not ALLOWED.match(p):
                problems.append(f"touches {p}, which a fix may not change")
                continue
            try:
                value = json.loads(str(op.get("value_json", "null")))
            except json.JSONDecodeError:
                problems.append(f"value for {p} is not JSON")
                continue
            ops.append({"op": op.get("op"), "path": p, "value": value})
        if problems:
            return Proposal(False, None, str(data.get("explanation", "")), problems)
        try:
            after = apply_patch(obj, ops)
        except (KeyError, IndexError, ValueError, TypeError) as err:
            return Proposal(False, None, str(data.get("explanation", "")), [f"patch does not apply: {err}"])
        result = verify(obj, after)
        not_fixed = [r for r in rules if r not in result.fixed]
        problems += [f"{r} is still violated" for r in not_fixed]
        problems += [f"introduces {r}" for r in sorted(result.introduced)]
        return Proposal(not problems, result, str(data.get("explanation", "")), problems)
