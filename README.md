# AlienApe

**Beta candidate: 0.7.3b1. This checkout is prepared for review; no tag, push, binary, or public release has been made.** v0.7.2 also remains unreleased, and v0.7.1 is frozen. Project Builder, model qualification, and Community Cards remain experimental; Project Builder research documents protocol-reliability limits.

Community Cards provide an offline JSON exchange for individual local Experience observations. The workflow has been smoke-tested with synthetic data. Imported cards remain `UNVERIFIED` and do not influence routing. This is not a model benchmark or global ranking system. See the [Community Cards WIP guide](docs/COMMUNITY_CARDS.md).

AlienApe is an experimental local AI controller for Windows. It coordinates local language models, controlled file operations, permissions, task routing, verification and local model experience through a native PyQt6 interface. The main workspace, settings (including Semantic Resolver), permission prompts, and Community Cards controls are localized in German, English, and French. Model-profile and Experience screens, plus some dynamic activity and controller status text, still contain German text. Model prompts ask for explanations in the language of the current request while preserving literal content and protocol fields; language quality depends on the configured model.

Code generation and source discovery recognize common programming languages and file extensions (including Python, JavaScript/TypeScript, C/C++, Java, C#, Rust, Go, Ruby, PHP, Swift, Kotlin, shell, SQL and others). This recognition does not promise model competence for every language. Automatic code verification remains limited to the existing Python and Godot checks; other languages are explicitly reported as not verified. AlienApe does not run arbitrary compilers or shell commands.

**Early experimental software. Expect rough edges. Do not trust it with irreplaceable data without backups.**

Requires Python 3.12+, PyQt6 and dependencies declared in `pyproject.toml`. From the source directory: `python -m pip install -e ".[test]"`, `python -m alienape`, and `python -m pytest`. Install and configure Ollama separately if desired; models are user-configurable and never downloaded by setup. Set `ALIENAPE_DATA_DIR` to a dedicated runtime folder. The default uses local user application data; existing portable installations with an executable-adjacent `data` directory remain supported. Use a different data directory for tests.

Diagnostic tracing is optional. Exports contain redacted chat, trace, summary and manifest. Existing target archives are not overwritten. See SECURITY.md and KNOWN_LIMITATIONS.md.

Full historical migration tests optionally require the authoritative archived v0.5 Experience fixture via `ALIENAPE_HISTORICAL_EXPERIENCE`; it is not distributed because it is historical user data. Without it, those two tests skip.

The source code is licensed under 0BSD; see [LICENSE](LICENSE). The optional mascot is separately designated CC0 1.0 by the project owner; see [ASSETS.md](ASSETS.md).

## Windows development and build

```cmd
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -e ".[test]"
python -m pytest
python -m alienape
```

A compiled Windows build is optional for a source-code upload. To build it, install the `build` extra (`python -m pip install -e ".[test,build]"`) and run `powershell -NoProfile -File scripts\build_windows.ps1`.

The onedir build is written to `dist/AlienApe/AlienApe.exe`. The optional `-RunTests` switch runs tests first with isolated runtime data. Two historical migration tests require the external authoritative fixture documented above. No model is downloaded. The source repository contains code, tests, documentation, resources and the build definition; an optional compiled build belongs in a separate release, never in Git. Runtime Memory, Experience, configuration and diagnostics remain outside the source/binary bundle.

## Further reading

- [Architecture](docs/ARCHITECTURE.md)
- [Community Cards WIP guide](docs/COMMUNITY_CARDS.md)
- [AI handoff](docs/AI_HANDOFF.md)
- [Prompt for your AI assistant](docs/USER_AI_PROMPT.md)
- [Known limitations](KNOWN_LIMITATIONS.md)
- [Security](SECURITY.md)

## Persistent Memory

Explicit commands such as `merk dir: ...` or `remember that ...` save a bounded item for the active project; ordinary conversation and chat transcripts are not automatically saved. Ask `was hast du dir gemerkt?` or `what do you remember?` for a bounded overview. `vergiss <distinctive words>` removes one uniquely matching, non-sensitive active project record; ambiguous matches do not delete anything. Memory OFF prevents use and writes without deleting stored records. Secrets are rejected on write and sensitive historical values are masked on retrieval; this is not a password manager. Lexical relevance is bounded and can miss or select imperfect matches. Session continuity and model Experience remain separate from persistent Memory.
