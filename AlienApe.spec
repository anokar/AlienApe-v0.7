# PyInstaller onedir build; only checked-in public resources are bundled.
from pathlib import Path
root = Path(SPECPATH)
catalogs = [(str(root / 'src' / 'alienape' / 'i18n' / (lang + '.json')), 'alienape/i18n') for lang in ('de', 'en', 'fr')]
schemas = [(str(p), '.') for p in root.glob('*.json') if p.name in {'BENCHMARK_CATALOG_V2.json', 'CANONICAL_INTENTS_V1.json', 'COMMUNITY_MODEL_CARD_V1.json', 'DIAGNOSTIC_SCHEMA_V1.json', 'MODEL_PROFILE_V2_SCHEMA.json', 'PROTOCOL_PROBE_V1.json', 'SEMANTIC_EVAL_V1.json', 'SEMANTIC_INTENT_SCHEMA_V1.json'}]
mascot = [(str(root / 'assets' / 'splash.png'), 'assets')] if (root / 'assets' / 'splash.png').is_file() else []
a = Analysis([str(root / 'run_app.py')], pathex=[str(root / 'src')], binaries=[], datas=catalogs + schemas + mascot + [(str(root / 'demo_workspace'), 'demo_workspace')], hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AlienApe', debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='AlienApe')
