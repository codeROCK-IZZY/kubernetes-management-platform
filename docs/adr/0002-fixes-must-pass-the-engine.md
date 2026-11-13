# 2. Every fix, automatic or from Claude, must pass the policy engine again

## Context

Auto-remediation that "fixes" a manifest can break it in another way: removing a capability the application
needed, adding resources to an ephemeral container (which Kubernetes rejects), or a language model changing the
workload's name while it is there.

## Decision

- **Automatic fixes** only for rules with one safe answer: not privileged, no privilege escalation, a non-root user,
  no dangerous capabilities, and requests and limits taken from the rightsizing recommendation. The original object
  is never modified; the fix works on a copy.
- **Claude** (`claude-opus-5-5`, low effort, cached system prompt, refusal fallbacks) proposes a JSON Patch for the
  rest through a strict tool. Each operation's path must match an allow-list: container image, security context,
  resources and probes; pod volumes and host namespace settings. Anything else rejects the whole patch.
- **Verification:** the patched copy is checked again. A fix is accepted only if every targeted violation is gone
  and no new one appears.

## Consequences

- Across the three banks, automatic fixes repaired all 829 auto-fixable violations and introduced none.
- The safe fixes can still be wrong for a specific application (a container that truly needs `NET_ADMIN`). The
  dashboard shows the fixed manifest for review; nothing is applied to a cluster.
- Claude's patches are tested through the real SDK with canned responses; CI makes no API calls.
