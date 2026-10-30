# Security

Workspace paths are constrained; WriteScope and permission checks gate mutations. Provider credentials use secret references and supported OS credential storage. Redaction protects logs and diagnostic bundles; diagnostic export writes redacted entries to a temporary archive and atomically publishes without overwriting. Memory applies sensitivity filtering. Provider and diagnostic redaction are separate boundaries. These controls do not constitute a full operating-system sandbox; filesystem time-of-check/time-of-use limitations can remain.

Do not post secrets or exploitation details publicly. Contact the project owner privately through an established trusted channel to report a vulnerability; no dedicated security address has yet been configured.
