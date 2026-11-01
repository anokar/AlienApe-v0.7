# AlienApe

> **Status: experimental, built for myself, now looking for a community.**
> AlienApe started as a private agent tuned to my own setup (German Windows, local models via Ollama / LM Studio). I'm not planning to keep developing it alone. If it's useful to you, fork it, break it, extend it. If not, it may quietly disappear into the void :)

## The idea: a small kernel for local AI agents

Think of AlienApe as a tiny **operating-system kernel for an AI agent**, not as a finished product.

- **The model is untrusted.** Paths, permissions, write scope, verification and redaction are enforced by ordinary code (the "controller"), never by the model.
- **Do with your PC what you don't need to pay tokens for.** File viewing, directory listing, memory, path checks, syntax checks and refusing unsupported requests all run locally without a model call. The goal is to save tokens (cloud) or time and energy (local) by sending only real thinking work to the model. This is the design intent. I have not benchmarked it yet. A comparison against a plain agent loop would be a great contribution.
- **"Written" is not "correct".** Writing a file, verifying it and attributing failures (controller, missing information, model protocol, resources) are separate steps.

## Where it could go

If the kernel is stable, the interesting part starts: **modules** on top of it, for example an MCP bridge, web search, more verifiers or other tools, each going through the same permission and verification layer. Writing an operating system takes a long time; this is the first, buggy boot.

## Good places to start

- Make the tests independent of the system language.
- Measure model calls / tokens against a simple baseline agent.
- Improve intent detection beyond fixed patterns.
- Add a first module (MCP, web search) behind the existing permission layer.

Found something broken? Please open an issue. That is exactly what this release is for.

**Development branch: v0.7.2 is work in progress and has not been released.** Project Builder and model qualification are experimental; field tests have exposed protocol reliability limitations.

AlienApe is an experimental local AI controller for Windows. It coordinates local language models, controlled file operations, permissions, task routing, verification and local model experience through a native PyQt6 interface. It is local-first; DE/EN/FR localization is partial and experimental. Model-generated output is not automatically translated, and rough edges are expected.

**Early experimental software. Expect rough edges. Do not trust it with irreplaceable data without backups.**

Requires Python 3.12+, PyQt6 and dependencies declared in `pyproject.toml`. From the source directory: `python -m pip install -e ".[test]"`, `python -m alienape`, and `python -m pytest`. Install and configure Ollama separately if desired; models are user-configurable and never downloaded by setup. Set `ALIENAPE_DATA_DIR` to a dedicated runtime folder. The default uses local user application data; existing portable installations with an executable-adjacent `data` directory remain supported. Use a different data directory for tests.

Diagnostic tracing is optional. Exports contain redacted chat, trace, summary and manifest. Existing target archives are not overwritten. See SECURITY.md and KNOWN_LIMITATIONS.md.

Full historical migration tests optionally require the authoritative archived v0.5 Experience fixture via `ALIENAPE_HISTORICAL_EXPERIENCE`; it is not distributed because it is historical user data. Without it, those two tests skip.

The source code is licensed under 0BSD; see [LICENSE](LICENSE). The optional mascot is separately designated CC0 1.0 by the project owner; see [ASSETS.md](ASSETS.md).

## Windows development and build

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,build]"
python -m pytest
python -m alienape
.\scripts\build_windows.ps1
```

The onedir build is written to `dist/AlienApe/AlienApe.exe`. The optional `-RunTests` switch runs tests first with isolated runtime data. Two historical migration tests require the external authoritative fixture documented above. No model is downloaded. The source repository contains code, tests, documentation, resources and the build definition; an optional compiled build belongs in a separate release, never in Git. Runtime Memory, Experience, configuration and diagnostics remain outside the source/binary bundle.

## Further reading

- [Architecture](docs/ARCHITECTURE.md)
- [AI handoff](docs/AI_HANDOFF.md)
- [Prompt for your AI assistant](docs/USER_AI_PROMPT.md)
- [Known limitations](KNOWN_LIMITATIONS.md)
- [Security](SECURITY.md)

## Persistent Memory

Explicit commands such as `merk dir: ...` or `remember that ...` save a bounded item for the active project; ordinary conversation and chat transcripts are not automatically saved. Ask `was hast du dir gemerkt?` or `what do you remember?` for a bounded overview. `vergiss <distinctive words>` removes one uniquely matching, non-sensitive active project record; ambiguous matches do not delete anything. Memory OFF prevents use and writes without deleting stored records. Secrets are rejected on write and sensitive historical values are masked on retrieval; this is not a password manager. Lexical relevance is bounded and can miss or select imperfect matches. Session continuity and model Experience remain separate from persistent Memory.
