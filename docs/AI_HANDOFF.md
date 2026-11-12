# AI handoff

## Current 0.7.3b1 beta candidate

- Status: source beta candidate prepared for review; it has not been tagged, pushed, or published. v0.7.2 remains unreleased; v0.7.1 remains frozen.
- The beta preparation keeps the existing controller and permission architecture. It adds request-language guidance to worker, compatibility, and project-planner prompts; localizes core workspace, settings, permission, Semantic Resolver, memory/activity labels, and Community Cards surfaces; and removes an unused resolver multilingual checkbox while preserving its saved config field.
- Code paths now share a source-language extension registry for routing, generation paths, discovery, source scoping, and fenced artifact continuity. Common languages beyond Python/Godot can be generated and inspected; automatic verification remains limited to Python/Godot, with no arbitrary shell or compiler execution.
- Changing the UI language takes effect after restart so the current window does not mix newly translated and old labels. Model-profile and Experience screens plus some dynamic event/status text remain German.
- Package metadata is PEP 440 pre-release `0.7.3b1`. No compiled binary, Git tag, push, or public release has been prepared.
- Full beta-candidate regression after language support: **1,118 passed, 0 failed, 5 skipped in 115.97s** using the project virtual environment and an isolated `work/` pytest temp directory. The new language-specific routing, discovery, safety, and Ruby file-generation checks passed.
- Language prompts were checked for exact user-text/code/path preservation guidance and unchanged permission/scope language. Prompt following by real models was not tested because no inference run was performed.
- Earlier candidate evidence below refers to the v0.7.3 source candidate before this beta-preparation work.
- User-run full test suite for the candidate application and tests: **1,110 passed, 0 failed, 4 skipped in 104.25s**, Python 3.12.10, pytest 9.2.0, Godot 4.7.2 explicitly configured. The four skips were two Windows symlink-permission cases and two historical Experience fixture cases. This handoff-only update and its derived build ID came afterward; no application or test code changed.
- Experience isolation was verified in separate processes across restarts with synthetic A1/B1 rows and separate `ALIENAPE_DATA_DIR` roots. No Community Card was imported during this test.
- The Community Card two-installation UI roundtrip passed in the full suite: preview and explicit confirmation in A, manual import in B, `UNVERIFIED` storage, duplicate detection, no routing evidence, and unchanged local Experience in B. Fixtures are synthetic; publisher authenticity was not tested.
- Community Cards are offline, one observation per JSON file, stored separately from local Experience, and never consumed by routing or GOOD_ENOUGH. They are unsigned and unverified; SHA-256 detects content changes but does not authenticate a publisher.
- The narrow file-intent correction recognizes explicit write/save wording with a named parsed target; WriteScope remains controller-owned and exact. Educational `wie/how` requests stay read-only.
- Model evidence is limited: DeepSeek Coder 6.7B FILE_CONTENT qualification was 4/5 over five calls; its FILE_PROPOSAL direct probe passed 1/3 protocol/target checks. All four configured profiles still have `code_generation=UNKNOWN` and `file_proposal=UNKNOWN`; no routing capability change is justified. The FILE_PROPOSAL probe did not preserve raw responses.
- No compiled Windows binary is included. README documents source installation; the Windows onedir build is optional and belongs in a separate distribution. No tag, push, or release has occurred.

AlienApe remains an experimental local-first controller. Capability is distinct from protocol adherence and quality; a successful write does not prove correctness. Memory, Session Context, local Experience, and Community Evidence remain separate concepts. No full operating-system sandbox exists. Model response language is prompt-guided and depends on model support; no automatic response translation is performed.


## Remaining release review

- Package metadata identifies `0.7.3b1`; no release tag or binary is prepared. README documents the Windows onedir build as optional and separate from the source repository.
- `src/alienape/build_identity.py` contains a Git-normalized source manifest ID. `scripts/update_build_identity.py` reproduces it from tracked and non-ignored source files, excluding itself and `work/` artifacts. Rerun it after source edits and before a distributable build.
- The prior v0.7.3 source-candidate full-suite result was 1,110 passed and 4 skipped. Current beta-preparation test results above supersede it for this tree.
- Four known skips remain: two Windows symlink-permission cases and two unavailable historical fixture cases. They are documented limitations, not claimed passes.
- Capability declarations remain UNKNOWN for the four configured local model profiles; the direct probes do not qualify them. No routing claims or model-name special cases were added.


Historical FILE_CONTENT context findings remain in [the audit note](FILE_CONTENT_CONTEXT_AUDIT.md). DeepSeek Coder 6.7B reached 4/5 on the concise fixed qualification but repeatedly produced `FORMAT_FAILURE` in the richer Project Builder context; the packet remained within its configured context budget. The purpose-line-only effect remains inconclusive, and the three parser-replayed qualification responses were byte-identical. Fingerprint consistency was checked, but historical model-binary provenance remains partly open. Archived raw captures and databases remain outside Git. Preserve these distinctions and do not claim broad model reliability from them.

Next work should stay bounded: use the documented offline workflow, address concrete usability or privacy issues with focused evidence, and keep Community Evidence separate. Do not add network sync, signatures/trust infrastructure, aggregation, rankings, routing effects, or a general security architecture without a measured need.

AlienApe is an experimental local-first native AI controller. The controller handles task interpretation, bounded context and routing, permissions, tools, verification and attribution. Workers are untrusted: controller-enforced capabilities, WriteScope and permissions determine what can actually happen.

GOOD_ENOUGH favors a suitable candidate supported by available evidence; it does not claim an optimal result. Capability is distinct from protocol adherence and quality. Memory stores project information, Experience records local observations, and Community Evidence stores imported Community Cards separately; imported cards remain unverified and unused by routing. A successful write is not proof of correct code. Attribution must distinguish controller, information, model/protocol and resource failures.

Keep productive settings, Memory, Experience, credentials and diagnostic data outside the source and isolated from tests. Changes must preserve path restrictions, permissions, redaction and verification. DE/EN/FR localization covers primary surfaces, with German text remaining in advanced model/Experience and some dynamic status areas. Model output follows a prompt language preference when the model supports it; it is not automatically translated. There is no complete operating-system sandbox.

Before proposing work, inspect the repository and read README.md, docs/ARCHITECTURE.md, KNOWN_LIMITATIONS.md, SECURITY.md, CHANGELOG.md and tests relevant to the subsystem. Repository state is authoritative; do not blindly implement roadmap ideas.

For new ideas: challenge the idea, build the smallest experiment, compare with a simpler baseline, measure benefit, cost and failure, and retain only complexity that earns its cost. Report evidence, limitations and what was not tested.

Memory V1 is explicit and project-bound; keep session continuity and model Experience separate. Never turn ordinary conversation into automatic permanent memory. Forgetting is restricted to one uniquely identified Memory record, not filesystem deletion.
