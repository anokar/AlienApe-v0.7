# Known limitations

- There is no general autonomous planner, deletion tool, general shell or Multi-Agent execution. Compound requests are intentionally bounded.
- Automatic code verification is currently limited to Python and Godot. Other supported language files may be generated, but AlienApe does not compile or execute them; a successful write is not proof of correctness.
- Models can violate output contracts; routing quality depends on available evidence. The semantic resolver is advisory and conservative.
- There is no full operating-system sandbox. An analysis-quality sanity guard is not generally available.
- Community Cards support an experimental offline, one-observation JSON workflow. Imports remain separate and `UNVERIFIED`; cards are unsigned, are not authenticated, do not affect routing, and are not synchronized or aggregated. Privacy screening for user-authored descriptions is heuristic and still requires review.
- Memory relevance uses bounded lexical matching; global Memory is not yet available and project association follows the active project store.
- DE/EN/FR localization covers the main workspace, settings (including Semantic Resolver), permission prompts, and Community Cards controls. Model-profile and Experience screens, plus some dynamic activity and controller status text, still contain German text. The current request language is supplied to model prompts; response language quality depends on the model and is not independently verified. Literal user text, code, paths, and output-contract fields are instructed to remain unchanged.
- The mascot is optional; the application supports a text-only header when it is absent. Its separate CC0 1.0 designation is documented in ASSETS.md.
