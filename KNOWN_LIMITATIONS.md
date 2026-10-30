# Known limitations

- There is no general autonomous planner, deletion tool, general shell or Multi-Agent execution. Compound requests are intentionally bounded.
- C/C++ objective verification may be NOT_RUN; a successful write is not proof of correctness.
- Models can violate output contracts; routing quality depends on available evidence. The semantic resolver is advisory and conservative.
- There is no full operating-system sandbox. An analysis-quality sanity guard is not generally available.
- Community evidence is scaffolding, off by default, and is not a network-enabled swarm.
- Memory relevance uses bounded lexical matching; global Memory is not yet available and project association follows the active project store.
- DE/EN/FR localization is partial and experimental for selected controller responses and UI surfaces. Some static text remains in the fallback language. Worker/model output is not automatically translated.
- The mascot is optional; the application supports a text-only header when it is absent. Its separate CC0 1.0 designation is documented in ASSETS.md.
