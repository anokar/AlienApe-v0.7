import pytest
from alienape.project_request import project_request


@pytest.mark.parametrize('text',['mach mir ein kleines python projekt mit einem test','erstelle eine kleine website mit html css und javascript','create a small PyQt6 project with an app and tests','bitte mach ein python projekt mit test'])
def test_project_request(text):
    assert project_request(text) is not None


@pytest.mark.parametrize('text',['erstelle hello.py','mach einen ordner test','schreib mir eine funktion','erstelle index.html','analysiere das projekt','erstelle kein projekt'])
def test_non_project_request(text):
    assert project_request(text) is None


@pytest.mark.parametrize('text',[
    'The tests in this Python project are failing. Find the cause, fix the implementation, change only the necessary source file, and make the existing tests pass.',
    'The tests in this Python project are failing. Find the cause, fix the implementation.',
    'Refactor the implementation to remove duplicated logic. Do not change the tests.',
])
def test_existing_code_work_is_not_new_project_request(text):
    assert project_request(text) is None


def test_explicit_project_root():
    assert project_request('mach ein python projekt namens calculator mit test').root=='calculator'
    assert project_request('create a small site with html css and javascript in monkey-web').root=='monkey-web'
    assert project_request('mach ein python projekt mit test').root is None
