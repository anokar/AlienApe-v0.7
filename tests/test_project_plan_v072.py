import json
import pytest
from alienape.project_plan import validate_plan, InvalidProjectPlan, MAX_FILES
from alienape.tools import Workspace


def file(path, depends_on=()):
    return {'path':path,'purpose':'test','language':'python','depends_on':list(depends_on)}


def validate(tmp_path, files):
    return validate_plan(json.dumps({'files':files}), Workspace(tmp_path), 'project', 'goal')


def test_directories_and_dependencies_are_ordered(tmp_path):
    plan=validate(tmp_path,[file('tests/test_app.py',['src/app.py']),file('src/app.py')])
    assert plan.execution_order == ('src','tests','src/app.py','tests/test_app.py')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('path',['../outside.txt','/absolute','C:/outside','foo//x','foo/../bar','NUL','foo\\bar'])
def test_unsafe_paths_rejected_before_mutation(tmp_path,path):
    with pytest.raises(InvalidProjectPlan): validate(tmp_path,[file(path)])
    assert list(tmp_path.iterdir()) == []


def test_duplicate_conflict_and_limit(tmp_path):
    for files in ([file('a.py'),file('A.py')],[file('a'),file('a/b')],[file(str(i)+'.py') for i in range(MAX_FILES+1)]):
        with pytest.raises(InvalidProjectPlan): validate(tmp_path,files)


def test_missing_and_cyclic_dependencies(tmp_path):
    for files in ([file('a.py',['missing.py'])],[file('a.py',['b.py']),file('b.py',['a.py'])]):
        with pytest.raises(InvalidProjectPlan): validate(tmp_path,files)


def test_unsupported_actions_and_root_override_rejected(tmp_path):
    for raw in ({'files':[file('a.py')], 'shell':'echo x'}, {'project_root':'elsewhere','files':[file('a.py')]}, {'files':[dict(file('a.py'), operation='delete')]}):
        with pytest.raises(InvalidProjectPlan): validate_plan(json.dumps(raw),Workspace(tmp_path),'project','goal')


@pytest.mark.parametrize('path',['../outside.py','..\\outside.py','C:\\outside.py','D:/outside.py','src/..\\outside.py','bad?.py'])
def test_windows_escape_variants_are_rejected(tmp_path,path):
    with pytest.raises(InvalidProjectPlan): validate(tmp_path,[file(path)])
    assert list(tmp_path.iterdir()) == []


def test_case_ambiguous_directories_rejected(tmp_path):
    with pytest.raises(InvalidProjectPlan): validate(tmp_path,[file('Src/a.py'),file('src/b.py')])


def test_symlink_escape_rejected_when_supported(tmp_path):
    outside=tmp_path / 'outside'
    outside.mkdir()
    root=tmp_path / 'project'
    root.mkdir()
    try:
        (root / 'link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Windows symlink creation is unavailable')
    with pytest.raises(InvalidProjectPlan): validate(tmp_path,[file('link/escape.py')])
    assert list(outside.iterdir()) == []
