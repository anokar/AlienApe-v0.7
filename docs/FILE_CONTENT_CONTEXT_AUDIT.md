# FILE_CONTENT context audit

Status: research note for the unreleased v0.7.2 development branch. No production code, profile, routing policy, threshold, or model setting was changed. No model inference was run for this audit.

## Question

Why did DeepSeek Coder 6.7B pass the concise FILE_CONTENT qualification but fail the real Project Builder request, and what does the archived evidence establish?

## Repository and method

- Authoritative source: Git worktree `wip/project-builder-v0.7.2`, commit `896778928c46b9f61fd4e30f2ca353a2ba2f9a0d` (2026-10-30). The sibling `outputs/AlienApe-v0.7.2` is a non-Git snapshot; its `agent.py`, `training.py`, `contracts.py`, and `experience.py` match this commit byte-for-byte.
- Replayed the three archived qualification responses through `Agent.call_model` from this commit, using the captured FILE_CONTENT packet and the existing qualification task validator. The send function returned the archived response; it did not contact a model. The replay used a temporary empty workspace.
- Read-only inspection of capture and qualification SQLite databases and both qualification CSV runs. Request comparison used the archived serialized model-bound messages.

The repository remains explicitly NOT RELEASED. v0.7.1 is a separate frozen artifact.

## Parser check

All three files `work/v072-mismatch-probe2/qualification-{1,2,3}-raw.json` have identical backend content SHA-256:

`341400c401869ce04d3d5691c3312b1665c391911f25b09c75d095ca26a3a160`

Each response is a `stop` completion containing exactly one JSON object with one non-empty string field, `content`; the value is valid Python source. The actual v0.7.2 parser accepted all three and set independent protocol status to `VERIFIED_SUCCESS` (3/3). This closes the previously unperformed parser replay for these archived bytes. It is not three independent model samples: the raw payloads are byte-identical.

## Qualification evidence

The normal corrected TrainingRunner artifact is `work/v072-transport-v2-corrected/benchmarks/83ef35daf85c410789192f8756a6da28/benchmark_results.csv`, with matching `experience.sqlite3`.

- The completed run has 40 CSV rows across eight profiles, five fixed FILE_CONTENT or native-tool cases per profile. The SQLite run row says `file_transport`, `COMPLETED`, 40 planned and 40 completed.
- DeepSeek Coder's five FILE_CONTENT rows contain four `VERIFIED_SUCCESS` task and protocol outcomes and one `FORMAT_FAILURE` with protocol `VERIFIED_FAILURE`. The row-level SQLite records agree. There are no output-limit/resource-neutral cases among those five.
- The existing qualification policy used five as its minimum and 0.8 as the threshold; therefore 4/5 meets the policy's `SUPPORTED` boundary. This is evidence for that fixed, concise suite and exact recorded fingerprint. It does not establish reliability on the actual Project Builder packet.
- The earlier `work/v072-transport-v2-fresh` run was invalidated: its fifth qualification case was mistakenly treated as requiring an input file. Its SQLite/CSV must not be combined with the corrected run.

The project-field report (`TRANSPORT_PREDICTIVE_REPORT.md` in the sibling research snapshot) records one actual calculator request: one DeepSeek FILE_CONTENT call, `stop`, `FORMAT_FAILURE`, zero writes, dependent test skipped, verification `NOT_RUN`. The raw response from that original field call was not retained, so its exact formatting subtype cannot be recovered from that field trace. The later exact-context replay did retain raw responses and produced 0/3 protocol successes; these repeats are correlated and should not be treated as three independent field trials.

## Capture SQLite and fingerprint

`work/v072-mismatch-probe2/capture.sqlite3` is a request-capture/identity database, not the qualification results database: it contains one fingerprint row and one profile-identity link, but zero `model_experience` rows and zero `benchmark_runs` rows. It cannot substantiate the 4/5 qualification result.

The capture stores the canonical identity fields: provider `ollama`, model `deepseek-coder:6.7b`, tag `6.7b`, local endpoint, backend version `0.40.11`, digest `ce298d984115b93bb1b191b47fee6b39e4e9fbd5f18e651c02f9fa74e0edcd13`, quantization `Q4_0`, temperature `0`, output budget `1024`, context budget `16000`, context length `16384`, and `model_version: null`. Re-serializing those stored identity fields with the current fingerprint canonicalization recomputes the stored fingerprint exactly:

`dd92262d99a46f40965edaa075be4fa84375e224b9dc578f539133eed4667b76`

This establishes internal consistency of the stored fingerprint record. It does not establish historical model-binary provenance independently: `model_version` is absent, and the capture does not include a signed/immutable model manifest or server-side identity proof. Preserve the provenance question as unresolved.

## Request diff

Archived messages are 2,180 UTF-8 serialized bytes for qualification and 2,897 bytes for the project request. The system message is byte-identical; both use model `deepseek-coder:6.7b`, FILE_CONTENT, no tools, 1,024 output tokens, temperature 0, and empty Memory/file/tool-result inputs. No fixed seed is recorded.

The user packet differs in several coupled fields:

| Field | Qualification | Project request |
|---|---|---|
| `task` | Short English arithmetic task explicitly repeats the JSON-only instruction | “Create the approved file” plus German project goal and a `Purpose` sentence |
| `task_type` | `BENCHMARK` | `CODE_TASK` |
| required capabilities | `PYTHON_CODING` | `coding` |
| constraints | empty | Proposal-only rule, one allowed nested target, and controller capability/scope text |
| resource budget | 2 model steps | 20 model steps |
| `expects_plain_text` | false | true |
| write scope | `target_1.py` | `calculator/src/calculator.py`, with planned new path |

Thus this is not a controlled test of byte length alone. It changes task framing, task class, capability label, constraints, plain-text expectation, target path and write-scope metadata together. The larger project packet remains well below its configured 16,000-byte budget; this evidence does not show a technical context-window overflow.

## Ablations and limits

Archived replay evidence in `PREDICTIVE_MISMATCH_PROBE_REPORT.md` and `work/v072-mismatch-probe2/` reports:

- exact qualification context: 3/3 protocol successes;
- exact project context: 0/3, all `FORMAT_FAILURE`;
- qualification context with its duplicate JSON instruction removed: 2/2 successes;
- qualification context with the task field replaced by the project goal plus JSON instruction: 0/2; both outer JSON objects had fenced file content;
- project wording with only “and a test” removed: one output-limit/neutral result, not evidence of a fix.

The task-field swap is sufficient to reproduce failure in this small replay, but it bundles project goal, wording and purpose. The archived artifacts do not contain a purpose-line-only removal arm, so they cannot establish that the `Purpose` line alone was tested or that its isolated effect was zero. Do not attribute causality to individual packet fields from these runs.

The small, repeated sample, fixed temperature without a fixed seed, and lack of independent seeds limit statistical interpretation. Current evidence supports a narrow conclusion: the same recorded model/settings can satisfy the minimal qualification prompt and fail the richer Project Builder packet's FILE_CONTENT envelope. It does not show that all project prompts fail, that the model lacks coding ability, or that context-window capacity was exhausted. Keep the strict protocol and the `NOT RELEASED` status unchanged.

## Evidence locations

The detailed replay and historical qualification files remain outside the source repository under `work/v072-mismatch-probe2/` and `work/v072-transport-v2-corrected/`. The reports copied into the non-Git research snapshot are not part of the authoritative Git tree. This note records the audited findings without importing runtime databases or raw model responses into Git.
