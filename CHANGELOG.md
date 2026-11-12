# Changelog

## 0.7.3b1 — beta candidate (not published)

- Add offline Community Cards for one-observation export and import, with a reviewable privacy preview, strict validation, separate `UNVERIFIED` Community Evidence storage, duplicate detection, and no routing effect.
- Harden display of imported untrusted card text.
- Correct Windows `German_Switzerland` locale recognition and classify explicit named-file write requests with an exact controller-owned WriteScope; educational questions remain read-only.
- Add isolated two-installation Experience and Community Card regression tests.
- Ask worker and planner prompts to use the current request language while preserving literal text, code, paths, and response contracts.
- Recognize common programming-language source files consistently in routing, generation requests, workspace discovery, and source-scope resolution. Keep automatic verification limited to the existing Python and Godot checks.
- Localize the main workspace, core memory/activity labels, settings, permissions, and Community Cards in DE/EN/FR; require restart after a UI-language change to avoid mixed-language windows.
- Remove the unused Semantic Resolver multilingual toggle while preserving its saved value for configuration compatibility.
- Mark package metadata as the 0.7.3 beta pre-release. Project Builder, model qualification, and remaining advanced-screen localization stay experimental; see Known Limitations and the Community Cards guide.
- Run the complete regression suite for this beta candidate: 1,118 passed, 0 failed, 5 skipped.


## 0.7.1

Explicit project Memory work, deterministic English remember and bounded single-record forget.

## 0.7.0

Clean source baseline, initial localization work, atomic diagnostic export preservation and English developer documentation built on the v0.6.9.4 behavior. Localization remains partial and experimental. An optional image with unconfirmed redistribution rights has been excluded from the public candidate.

## 0.6.9.4

Bounded compound task and continuity repairs; atomic redacted diagnostic ZIP export.
