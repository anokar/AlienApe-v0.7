# AI handoff

## Current v0.7.3 development state

- Verified branch: `wip/community-cards`.
- Source baseline before documentation-only updates: `de98823ae982fc9d55fac57f9f2374fe77cf0abc`.
- Previous v0.7.2 failure-containment checkpoint: `42a505dda0ebbd7eaa95e8947cf1960711363137`; v0.7.2 remains unreleased and v0.7.1 remains frozen.
- Branch history after that checkpoint: locale/source-discovery fixes (`8679d56`), first Community Cards workflow (`ba94572`), and untrusted display hardening (`de98823`).
- Latest full-suite evidence: 1,102 passed, 0 failed, 4 skipped. Skips were two unavailable historical Experience fixtures and two Windows symlink-permission cases. Godot 4.7.2 was explicitly configured and its security test ran.
- The synthetic two-install offline Community Card smoke test passed: export preview and confirmation, separate import storage, preserved provenance, duplicate detection, hash-tamper rejection, and unchanged local Experience in the receiving installation. This says nothing about publisher authenticity.
- Project Builder failure-containment tests cover `FORMAT_FAILURE`, `WRITE_FAILURE`, `VERIFICATION_FAILURE`, and `RESOURCE_OUTPUT_LIMIT`; the latest full suite passed these alongside the rest of the repository tests.
- Community Card schema is 1.0.0. Imported cards are separate from local Experience, always `UNVERIFIED`, and have no routing/GOOD_ENOUGH effect. SHA-256 checks content integrity only.
- `pyproject.toml` still reports package version 0.7.2 while the v0.7.3 development branch is in progress; do not describe this branch as a released 0.7.3 package.

Historical FILE_CONTENT context findings remain in [the audit note](FILE_CONTENT_CONTEXT_AUDIT.md). DeepSeek Coder 6.7B reached 4/5 on the concise fixed qualification but repeatedly produced `FORMAT_FAILURE` in the richer Project Builder context; the packet remained within its configured context budget. The purpose-line-only effect remains inconclusive, and the three parser-replayed qualification responses were byte-identical. Fingerprint consistency was checked, but historical model-binary provenance remains partly open. Archived raw captures and databases remain outside Git. Preserve these distinctions and do not claim broad model reliability from them.

Next work should stay bounded: use the documented offline workflow, address concrete usability or privacy issues with focused evidence, and keep Community Evidence separate. Do not add network sync, signatures/trust infrastructure, aggregation, rankings, routing effects, or a general security architecture without a measured need.

AlienApe is an experimental local-first native AI controller. The controller handles task interpretation, bounded context and routing, permissions, tools, verification and attribution. Workers are untrusted: controller-enforced capabilities, WriteScope and permissions determine what can actually happen.

GOOD_ENOUGH favors a suitable candidate supported by available evidence; it does not claim an optimal result. Capability is distinct from protocol adherence and quality. Memory stores project information, Experience records local observations, and Community Evidence stores imported Community Cards separately; imported cards remain unverified and unused by routing. A successful write is not proof of correct code. Attribution must distinguish controller, information, model/protocol and resource failures.

Keep productive settings, Memory, Experience, credentials and diagnostic data outside the source and isolated from tests. Changes must preserve path restrictions, permissions, redaction and verification. Partial DE/EN/FR localization is experimental; model output is not automatically translated. There is no complete operating-system sandbox.

Before proposing work, inspect the repository and read README.md, docs/ARCHITECTURE.md, KNOWN_LIMITATIONS.md, SECURITY.md, CHANGELOG.md and tests relevant to the subsystem. Repository state is authoritative; do not blindly implement roadmap ideas.

For new ideas: challenge the idea, build the smallest experiment, compare with a simpler baseline, measure benefit, cost and failure, and retain only complexity that earns its cost. Report evidence, limitations and what was not tested.

Memory V1 is explicit and project-bound; keep session continuity and model Experience separate. Never turn ordinary conversation into automatic permanent memory. Forgetting is restricted to one uniquely identified Memory record, not filesystem deletion.
