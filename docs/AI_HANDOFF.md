# AI handoff

## Current v0.7.3 source candidate

- Status: source candidate prepared for review/upload, not released. v0.7.2 remains unreleased; v0.7.1 remains frozen.
- Based on `wip/community-cards` at `684e4e021a480672e16f09ec70a02d3fd5436026`; the candidate adds a narrow intent fix, two isolated-installation tests, version 0.7.3 metadata, and updated release documentation.
- User-run full test suite on the same source and test changes: **1,110 passed, 0 failed, 4 skipped**, Python 3.12.10, pytest 9.2.0, Godot 4.7.2 explicitly configured. The four skips were two Windows symlink-permission cases and two historical Experience fixture cases.
- Experience isolation was verified in separate processes across restarts with synthetic A1/B1 rows and separate `ALIENAPE_DATA_DIR` roots. No Community Card was imported during this test.
- The Community Card two-installation UI roundtrip passed in the full suite: preview and explicit confirmation in A, manual import in B, `UNVERIFIED` storage, duplicate detection, no routing evidence, and unchanged local Experience in B. Fixtures are synthetic; publisher authenticity was not tested.
- Community Cards are offline, one observation per JSON file, stored separately from local Experience, and never consumed by routing or GOOD_ENOUGH. They are unsigned and unverified; SHA-256 detects content changes but does not authenticate a publisher.
- The narrow file-intent correction recognizes explicit write/save wording with a named parsed target; WriteScope remains controller-owned and exact. Educational `wie/how` requests stay read-only.
- Model evidence is limited: DeepSeek Coder 6.7B FILE_CONTENT qualification was 4/5 over five calls; its FILE_PROPOSAL direct probe passed 1/3 protocol/target checks. All four configured profiles still have `code_generation=UNKNOWN` and `file_proposal=UNKNOWN`; no routing capability change is justified. The FILE_PROPOSAL probe did not preserve raw responses.
- No compiled Windows binary is included. README documents source installation; the Windows onedir build is optional and belongs in a separate distribution. No tag, push, or release has occurred. The 1,110-test full-suite result predates the version/documentation-only edits in this candidate folder; the final candidate has not been rerun in this environment.

AlienApe remains an experimental local-first controller. Capability is distinct from protocol adherence and quality; a successful write does not prove correctness. Memory, Session Context, local Experience, and Community Evidence remain separate concepts. No full operating-system sandbox exists, and localization remains partial/experimental.


## Remaining release review

- The package and UI version metadata now identify the v0.7.3 source candidate, but no release tag or binary is prepared. README says the Windows onedir build is optional and separate from the source repository.
- `src/alienape/build_identity.py` now has a v0.7.3 candidate identifier. `scripts/update_build_identity.py` reproduces it from Git-normalized blob IDs for tracked and non-ignored source files, excluding the identity file and `work/` artifacts. Rerun the helper after source edits and before a final distributable build.
- The full suite result is from the same application/test changes, before version-only and documentation changes in this new candidate. Run the final candidate from its own `.venv` before uploading if final-tree test evidence is required.
- Four known skips remain: two Windows symlink-permission cases and two unavailable historical fixture cases. They are documented limitations, not claimed passes.
- Capability declarations remain UNKNOWN for the four configured local model profiles; the direct probes do not qualify them. No routing claims or model-name special cases were added.


Historical FILE_CONTENT context findings remain in [the audit note](FILE_CONTENT_CONTEXT_AUDIT.md). DeepSeek Coder 6.7B reached 4/5 on the concise fixed qualification but repeatedly produced `FORMAT_FAILURE` in the richer Project Builder context; the packet remained within its configured context budget. The purpose-line-only effect remains inconclusive, and the three parser-replayed qualification responses were byte-identical. Fingerprint consistency was checked, but historical model-binary provenance remains partly open. Archived raw captures and databases remain outside Git. Preserve these distinctions and do not claim broad model reliability from them.

Next work should stay bounded: use the documented offline workflow, address concrete usability or privacy issues with focused evidence, and keep Community Evidence separate. Do not add network sync, signatures/trust infrastructure, aggregation, rankings, routing effects, or a general security architecture without a measured need.

AlienApe is an experimental local-first native AI controller. The controller handles task interpretation, bounded context and routing, permissions, tools, verification and attribution. Workers are untrusted: controller-enforced capabilities, WriteScope and permissions determine what can actually happen.

GOOD_ENOUGH favors a suitable candidate supported by available evidence; it does not claim an optimal result. Capability is distinct from protocol adherence and quality. Memory stores project information, Experience records local observations, and Community Evidence stores imported Community Cards separately; imported cards remain unverified and unused by routing. A successful write is not proof of correct code. Attribution must distinguish controller, information, model/protocol and resource failures.

Keep productive settings, Memory, Experience, credentials and diagnostic data outside the source and isolated from tests. Changes must preserve path restrictions, permissions, redaction and verification. Partial DE/EN/FR localization is experimental; model output is not automatically translated. There is no complete operating-system sandbox.

Before proposing work, inspect the repository and read README.md, docs/ARCHITECTURE.md, KNOWN_LIMITATIONS.md, SECURITY.md, CHANGELOG.md and tests relevant to the subsystem. Repository state is authoritative; do not blindly implement roadmap ideas.

For new ideas: challenge the idea, build the smallest experiment, compare with a simpler baseline, measure benefit, cost and failure, and retain only complexity that earns its cost. Report evidence, limitations and what was not tested.

Memory V1 is explicit and project-bound; keep session continuity and model Experience separate. Never turn ordinary conversation into automatic permanent memory. Forgetting is restricted to one uniquely identified Memory record, not filesystem deletion.
