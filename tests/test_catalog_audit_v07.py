import importlib.util
from pathlib import Path
from alienape.i18n import localize

def test_catalogs_have_identical_valid_ids_and_placeholders():
    path=Path(__file__).parents[1]/'scripts'/'audit_i18n.py'
    spec=importlib.util.spec_from_file_location('i18n_catalog_audit',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.audit()
    assert result['catalog_integrity_pass'],result
    assert localize('NONEXISTENT_INTERNAL_ID','fr')=='Message unavailable.'
