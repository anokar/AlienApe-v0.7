# AlienApe

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
