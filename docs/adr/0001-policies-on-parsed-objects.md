# 1. Policies run on parsed Kubernetes objects, and the test forms came after the engine

## Context

Many manifest checks search the YAML text: "privileged: true" anywhere, "limits:" present somewhere. Kubernetes
reads objects, not text. A pod spec can be nested two levels deep in a CronJob. A violation can sit in one of two
containers or only in an init container. A container's `runAsNonRoot: false` overrides the pod's `true`. "ALL" in
`capabilities.drop` is the safe setting. A comment saying "never set privileged: true" is not configuration.

## Decision

`policy.check` loads every document (multi-document files, `List` objects, YAML anchors and JSON included), unwraps
each workload kind to its pod spec, and checks:

- every container, init container and ephemeral container, with pod-level security settings inherited and
  overridden field by field;
- image references, parsed (digest, registry port, tag);
- probes, only where Kubernetes would run the container indefinitely;
- RBAC rules, for wildcard verbs and resources.

## How it was tested

The engine was written against the `dev` forms until it matched them exactly, then frozen. Only then were the
`fresh` forms written: CronJob and Job nesting, init and ephemeral containers, container overrides, digests and
registry ports, multi-document files, anchors, JSON and misleading comments. Each case states its violations from
Kubernetes semantics, not from the engine's output. The engine scored 1.0 on them, and text search 0.34.

Fair caveat: the same person wrote both, and the engine was designed from the Kubernetes specification. The fresh
bank shows that following the specification covers these forms. It does not prove there is no form the engine
misreads.

## Consequences

- Custom resources that embed pod templates (Argo Rollouts, Knative services) are not unwrapped yet.
- Policies are code, not a policy language. Teams that already use OPA Gatekeeper or Kyverno can treat this engine as
  a pre-commit check with the same intent.
