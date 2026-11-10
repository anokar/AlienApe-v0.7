# Community Cards — experimental WIP

Community Cards are an offline way to share one observation from local AlienApe Experience. They are not model benchmarks, global rankings, automatic qualifications, or executable instructions. This guide describes the v0.7.3 source candidate; v0.7.2 and v0.7.3 are not releases.

## Workflow

`LOCAL EXPERIENCE → select one observation → review/edit privacy preview → confirm → export JSON → import JSON → validate → separate Community Evidence storage → inspect as UNVERIFIED`

### Export

1. Open the **Community Cards** tab.
2. Select one eligible local observation in the table. The current UI offers primary `live` and `benchmark` Experience rows, one at a time.
3. Choose **Ausgewählte Beobachtung prüfen und exportieren …**.
4. Review and generalize **Allgemeine Beschreibung** and optionally add **Bekannte Einschränkungen**. The JSON preview updates as these fields change.
5. Select the explicit confirmation checkbox. Saving stays disabled until the checkbox is selected and the preview is valid.
6. Choose a JSON file destination. The app writes the file atomically; importing it elsewhere is a separate manual action.

### Import and inspection

1. On the receiving installation, open **Community Cards** and choose **JSON importieren …**.
2. Select the exported card. The importer accepts at most 64 KiB, validates the strict schema and content hash, and reports an import, duplicate, or validation error.
3. Select an imported row to inspect its fields. The table and detail view label cards `UNVERIFIED`.

Move the JSON file between installations yourself. There is no network synchronization. Imported cards are stored in `community_evidence.sqlite3`, separate from the installation's `model_experience.sqlite3`. Import never adds to, edits, or deletes local Experience.

## Card contents and privacy

Each version `1.0.0` card represents one observation. It includes a random card ID and timestamps; provider/model labels; nullable fingerprint, digest, quantization and backend metadata; generalized task category and user-authored description; response contract, transport and available execution conditions; distinct protocol, task-quality, verification and failure fields; limitations; and provenance (source, observation time, suite version when known, and application version).

Unknown identity or condition data remains null or `UNKNOWN`; a fingerprint is never inferred from a model name. Protocol adherence is separate from task quality. Resource and controller failures have separate failure classes and are not automatically turned into model-quality failures.

The exporter reads an explicit allowlist from an eligible Experience row. It does not export raw prompts, model answers, file contents, workspace paths, endpoint URLs, credentials, chat history, local Experience IDs, or database IDs. Description and limitation fields are newly entered and bounded to 240 and 500 characters. The preview applies heuristic privacy checks, and export requires explicit user confirmation.

The exact fingerprint is an opaque hash of the model/settings identity and may be stable for the same identity across installations. The endpoint string is not included, but the fingerprint should not be treated as anonymization. Descriptions require human review: automated screening cannot identify every personal or sensitive detail. Do not include information you do not intend to share.

## Validation, integrity and trust

Import rejects malformed JSON, duplicate JSON properties, unsupported schema versions, unexpected properties, invalid types, missing provenance, out-of-bounds fields, and cards larger than 64 KiB. It checks SHA-256 over canonical card content. Re-importing the same card is a duplicate; a different card reusing an existing card ID is a conflict. The importer does not repair a bad card.

SHA-256 detects content changes relative to the supplied hash; anyone can create a card and calculate a new hash. It does not authenticate the publisher or prove the observation happened. There are no signatures or cryptographic trust infrastructure. Imported data is never executed and does not influence `GOOD_ENOUGH`, model selection or routing, even if the legacy community source setting is enabled.

## Current limitations

- Cards are unverified observations, not benchmark evidence or an independent reproduction.
- There is no aggregation, ranking, automatic qualification, synchronization, or publisher authentication.
- Privacy screening is heuristic; user review remains necessary. Unicode display controls are made visible, but visually similar Unicode characters may still resemble one another.
- Cards use the current package metadata for `application_version` (`0.7.3` in this source candidate).
- The workflow has been smoke-tested with synthetic data in two isolated installations. That test does not establish real-world provenance or publisher authenticity.

For the controller's boundaries, see [Architecture](ARCHITECTURE.md), [Security](../SECURITY.md), and [Known limitations](../KNOWN_LIMITATIONS.md).
