# AI handoff

AlienApe is an experimental local-first native AI controller. The controller handles task interpretation, bounded context and routing, permissions, tools, verification and attribution. Workers are untrusted: controller-enforced capabilities, WriteScope and permissions determine what can actually happen.

GOOD_ENOUGH favors a suitable candidate supported by available evidence; it does not claim an optimal result. Capability is distinct from protocol adherence and quality. Memory stores project information, Experience records local observations, and Community Evidence is separate, disabled-by-default scaffolding. A successful write is not proof of correct code. Attribution must distinguish controller, information, model/protocol and resource failures.

Keep productive settings, Memory, Experience, credentials and diagnostic data outside the source and isolated from tests. Changes must preserve path restrictions, permissions, redaction and verification. Partial DE/EN/FR localization is experimental; model output is not automatically translated. There is no complete operating-system sandbox.

Before proposing work, inspect the repository and read README.md, docs/ARCHITECTURE.md, KNOWN_LIMITATIONS.md, SECURITY.md, CHANGELOG.md and tests relevant to the subsystem. Repository state is authoritative; do not blindly implement roadmap ideas.

For new ideas: challenge the idea, build the smallest experiment, compare with a simpler baseline, measure benefit, cost and failure, and retain only complexity that earns its cost. Report evidence, limitations and what was not tested.

Memory V1 is explicit and project-bound; keep session continuity and model Experience separate. Never turn ordinary conversation into automatic permanent memory. Forgetting is restricted to one uniquely identified Memory record, not filesystem deletion.
