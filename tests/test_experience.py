import asyncio
from dataclasses import replace
import json
import threading
import pytest
from PyQt6.QtWidgets import QDialog, QDialogButtonBox
from alienape.benchmark import BenchmarkRunner, BenchmarkDemoBackend, suite, validate_response, check_python
from alienape.config import Settings
from alienape.experience import ExperienceStore, ModelFingerprint, SuccessSignal as S
from alienape.experience_ui import BenchmarkDialog
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry, ModelRouter
from alienape.taxonomy import task_category, TaskCategory as C
from alienape.tools import Workspace
from alienape.worker import AgentWorker
from alienape.events import EventLog
from test_routing import profile
from test_routed_agent import Pool, RecordingMock, answer
from test_models_gui import multi_window
from test_gui import until


def register(store, p, **meta):
    fp = ModelFingerprint.for_profile(p, meta)
    store.register(p, fp)
    return fp.id


def populate(store, fp, category=C.PYTHON_DEBUG, good=9, bad=1, latency=500):
    for success in [True]*good + [False]*bad:
        store.record(fp, category, S.VERIFIED_SUCCESS if success else S.VERIFIED_FAILURE,
                     latency_ms=latency, input_tokens=100, output_tokens=20, context_bytes=300,
                     tool_calls=2, invalid_tool_calls=0 if success else 1, escalated=not success)


def test_success_failure_unknown_and_aggregation(tmp_path):
    store = ExperienceStore(tmp_path/'experience.db')
    fp = register(store, profile('a'))
    populate(store, fp, good=3, bad=1)
    store.record(fp, C.PYTHON_DEBUG, S.UNKNOWN, latency_ms=2500)
    row = store.summary(fp, C.PYTHON_DEBUG)
    assert row['sample_count'] == 5 and row['hard_sample_count'] == 4
    assert row['verified_success_count'] == 3 and row['verified_failure_count'] == 1
    assert row['success_rate'] == .75
    assert row['median_latency'] == 500 and row['mean_latency'] == 900
    assert row['median_input_tokens'] == 100 and row['median_output_tokens'] == 20
    assert row['invalid_tool_rate'] == 1/8 and row['escalation_rate'] == .2
    with store.connection() as db:
        cols = [r[1] for r in db.execute('PRAGMA table_info(model_experience)')]
        assert not {'answer', 'content', 'prompt'} & set(cols)
        assert db.execute('SELECT energy_joules FROM model_experience').fetchone()[0] is None


def test_feedback_is_explicit_and_replaces_old_opinion(tmp_path):
    store = ExperienceStore(tmp_path/'experience.db')
    fp = register(store, profile('a'))
    attempt = store.record(fp, C.GENERAL_SIMPLE)
    assert store.summary(fp, C.GENERAL_SIMPLE)['success_rate'] is None
    store.feedback(attempt, True)
    assert store.summary(fp, C.GENERAL_SIMPLE)['success_rate'] == 1
    store.feedback(attempt, False)
    row = store.summary(fp, C.GENERAL_SIMPLE)
    assert row['hard_sample_count'] == 1 and row['success_rate'] == 0
    assert row['user_failure_count'] == 1 and row['verified_failure_count'] == 0


def test_identity_settings_versions_and_stale_are_separate(tmp_path):
    store = ExperienceStore(tmp_path/'experience.db')
    p = profile('a')
    fp = ModelFingerprint.for_profile(p, {'digest': 'first', 'quantization': 'Q4'})
    store.register(p, fp)
    populate(store, fp.id)
    newer = replace(fp, digest='second')
    store.register(p, newer)
    assert store.summary(fp.id, C.PYTHON_DEBUG) is None
    assert store.summaries()[0]['stale']
    assert store.summary(newer.id, C.PYTHON_DEBUG) is None
    assert replace(fp, context_budget=20000).id != fp.id
    assert replace(fp, backend_version='2').id != fp.id
    assert replace(fp, quantization='Q8').id != fp.id
    assert ModelFingerprint.for_profile(p).digest is None
    store.record(newer.id, C.PYTHON_DEBUG, S.VERIFIED_SUCCESS, source='benchmark', suite_version=0)
    assert store.summary(newer.id, C.PYTHON_DEBUG)['hard_sample_count'] == 0
    store.mark_stale(newer.id, 'BENCHMARK_VERSION_CHANGED')
    assert store.summary(newer.id, C.PYTHON_DEBUG) is None


def routed_store(tmp_path):
    settings = Settings()
    settings.models = [profile('small', 'coding', 'low'), profile('coder', 'coding', 'medium'), profile('large', 'coding', 'high')]
    registry = ModelRegistry(settings.models)
    store = ExperienceStore(tmp_path/'experience.db')
    fps = {p.id: register(store, p) for p in settings.models}
    events = []
    router = ModelRouter(registry, settings.routing, store, fps, lambda k,d: events.append((k,d)))
    return settings, registry, store, fps, router, events


def test_good_enough_skip_quality_and_latency(tmp_path):
    settings, registry, store, fps, router, events = routed_store(tmp_path)
    populate(store, fps['small'], good=2, bad=8, latency=20)
    populate(store, fps['coder'], good=9, bad=1, latency=100)
    populate(store, fps['large'], good=10, bad=0, latency=400)
    assert router.select_model('Debug Python').profile.id == 'coder'
    assert any(k == 'MODEL_SKIPPED_FROM_EXPERIENCE' and d['model_id']=='small' for k,d in events)
    assert 'Erfahrung' in router.select_model('Debug Python').reason
    settings.routing.quality[str(C.PYTHON_DEBUG)] = .95
    assert router.select_model('Debug Python').profile.id == 'large'
    assert router.select_model('Debug Python', state={'manual_model':'small'}).profile.id == 'small'
    registry.profiles['large'].enabled = False
    assert router.select_model('Debug Python').profile.id != 'large'


def test_min_samples_cold_start_unknown_and_stale_fall_back(tmp_path):
    settings, registry, store, fps, router, _ = routed_store(tmp_path)
    populate(store, fps['small'], good=0, bad=3)
    populate(store, fps['coder'], good=3, bad=0)
    for _ in range(20):
        store.record(fps['coder'], C.PYTHON_DEBUG, S.UNKNOWN)
    assert router.select_model('Debug Python').profile.id == 'small'
    populate(store, fps['coder'], good=2, bad=0)
    assert router.select_model('Debug Python').profile.id == 'coder'
    assert router.select_model('Write Python').profile.id == 'small'
    store.mark_stale(fps['coder'])
    assert router.select_model('Debug Python').profile.id == 'small'


@pytest.mark.parametrize('text,category', [
    ('Debug player.py', C.PYTHON_DEBUG), ('Schreibe Python Code', C.PYTHON_CODING),
    ('Code review in x.js', C.CODE_REVIEW), ('Godot script', C.GODOT_CODE),
    ('Fasse die Notiz zusammen', C.SUMMARIZATION), ('JSON Schema', C.GENERAL_SIMPLE),
    ('Plane die Arbeit', C.PLANNING), ('Beweise die Aussage', C.REASONING),
    ('Analysiere notes.txt', C.FILE_ANALYSIS), ('Nutze ein Werkzeug', C.TOOL_USE), ('Hallo', C.GENERAL_SIMPLE)])
def test_taxonomy(text, category):
    assert task_category(text) == category


@pytest.mark.parametrize('code', [
    'import os\ndef solve(a,b): return a+b',
    'def solve(a,b): return __import__("os").system("anything")',
    'def solve(a,b):\n while True: pass',
    'def solve(a,b): return a**100000000',
    '@unknown\ndef solve(a,b): return a+b',
])
def test_python_benchmark_cannot_execute_generated_code(code):
    task = suite([C.PYTHON_CODING])[0]
    assert validate_response(task, answer(code)) == S.VERIFIED_FAILURE


def test_validators_objective_and_unrated():
    assert check_python('def solve(a,b): return a+b', '+')
    assert not check_python('def solve(a,b): return a*b', '+')
    task = suite([C.STRUCTURED_OUTPUT])[0]
    assert validate_response(task, answer('{"name":"Ada","count":3}')) == S.VERIFIED_SUCCESS
    assert validate_response(task, answer('{"name":"Ada","count":true}')) == S.VERIFIED_FAILURE
    task = suite([C.SUMMARIZATION])[0]
    assert validate_response(task, answer('I succeeded perfectly')) == S.UNKNOWN


class PerformanceMock(BenchmarkDemoBackend):
    def __init__(self, successes):
        super().__init__(delay=0)
        self.successes, self.count, self.prompts = successes, 0, []

    async def chat_with_mode(self, messages, tools=None, model='', mode='none'):
        self.prompts.append(json.dumps(messages))
        self.count += 1
        if self.count > self.successes:
            return answer('def solve(a,b): return 999')
        return await super().chat_with_mode(messages, tools, model, mode)


def test_mock_benchmark_to_routing_then_monotone_escalation(tmp_path):
    settings, registry, store, fps, router, events = routed_store(tmp_path)
    mocks = {key: PerformanceMock(n) for key,n in [('small',4),('coder',9),('large',10)]}
    pool = Pool(mocks)
    runner = BenchmarkRunner(registry, settings, store, list(mocks), [C.PYTHON_DEBUG], pool=pool)
    runner.scope='standard'  # Ten zero-compute fixture trials, not the bounded QUICK plan.
    runner.tasks = suite([C.PYTHON_DEBUG])*10
    asyncio.run(runner.run())
    assert [b.count for b in mocks.values()] == [10,10,10]
    assert mocks['small'].prompts == mocks['coder'].prompts == mocks['large'].prompts
    fps = {p.id: ModelFingerprint.for_profile(p, {'model_version':'demo-1','backend_version':'mock-1'}).id for p in settings.models}
    router.fingerprints = fps
    assert router.select_model('Debug Python').profile.id == 'coder'
    assert store.summary(fps['small'], C.PYTHON_DEBUG)['success_rate'] == .4
    models = {k: RecordingMock([answer('wrong' if k=='coder' else 'right')]) for k in mocks}
    live = RoutedAgent(Workspace(tmp_path), settings, registry=registry, pool=Pool(models), experience=store,
                       verify=lambda p, a, packet: a == 'right', observe=lambda k,d,*args: events.append((k,d)))
    result = asyncio.run(live.run('Debug Python'))
    assert result == 'right'
    assert len(models['small'].calls) == 0 and len(models['coder'].calls) == len(models['large'].calls) == 1
    assert store.summary(fps['coder'], C.PYTHON_DEBUG)['verified_failure_count'] == 2
    assert store.summary(fps['large'], C.PYTHON_DEBUG)['verified_success_count'] == 11
    transitions = [d for k,d in events if k == 'MODEL_ESCALATED']
    assert [(d['from_model'],d['to_model']) for d in transitions] == [('coder','large')]
    assert registry.profiles['small'].capabilities == ['coding','debugging']


def test_live_self_report_success_is_unknown(tmp_path):
    settings, registry, store, fps, _, _ = routed_store(tmp_path)
    models = {p.id: RecordingMock([answer('{"answer":"Solved!","status":"ok"}')]) for p in settings.models}
    settings.models[0].capabilities.append("general")  # General explanation fixture declares eligibility.
    agent = RoutedAgent(Workspace(tmp_path), settings, registry=registry, pool=Pool(models), experience=store)
    asyncio.run(agent.run('Explain Python'))
    row = store.summaries()[0]
    assert row['sample_count'] == 1 and row['hard_sample_count'] == 0
    assert row['success_rate'] is None


def test_benchmark_cancel_and_no_answer_cache(tmp_path):
    settings, registry, store, _, _, _ = routed_store(tmp_path)
    slow = RecordingMock([answer('12')], delay=10)
    runner = BenchmarkRunner(registry, settings, store, ['small'], [C.GENERAL_SIMPLE], pool=Pool({'small':slow}))
    async def stop():
        job = asyncio.create_task(runner.run())
        await asyncio.sleep(.02)
        runner.stopped.set()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    asyncio.run(stop())
    assert not runner.results
    fast = PerformanceMock(100)
    for _ in range(2):
        run = BenchmarkRunner(registry, settings, store, ['small'], [C.PYTHON_CODING], pool=Pool({'small':fast}))
        asyncio.run(run.run())
    assert fast.count == 2


def test_experience_gui_separates_declared_and_observed(multi_window):
    w = multi_window
    panel = w.experience_panel
    assert 'ANGEGEBENE' in panel.details.toPlainText() and 'GEMESSENE' in panel.details.toPlainText()
    p = w.settings.models[1]
    fp = register(w.experience, p)
    populate(w.experience, fp, good=2, bad=0)
    panel.refresh()
    assert panel.table.rowCount() == 1
    assert 'LOW_CONFIDENCE' in panel.table.item(0,7).text() and 'VERY_LOW' in panel.table.item(0,7).text()
    panel.table.selectRow(0)
    assert 'coding' in panel.details.toPlainText() and '2 verifizierte' in panel.details.toPlainText()
    dialog = BenchmarkDialog(w.registry)
    assert not dialog.controls.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    from PyQt6.QtCore import Qt
    dialog.models.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog.controls.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert 'primäre Aufgaben' in dialog.count.text() and dialog.scope.currentData()=='medium'


def test_benchmark_worker_stop_is_wired(multi_window):
    w = multi_window
    registry = w.registry
    first = next(iter(registry.profiles))
    backend = RecordingMock([answer('12')], delay=10)
    runner = BenchmarkRunner(registry, w.settings, w.experience, [first], [C.GENERAL_SIMPLE], pool=Pool({first:backend}))
    w.bind_worker(AgentWorker(agent=runner, log=w.event_log, permissions=w.permissions))
    from PyQt6.QtTest import QTest
    QTest.qWait(60)
    assert w.stop_button.isEnabled()
    w.tabs.setCurrentWidget(w.experience_panel)
    assert w.experience_panel.stop_button.isEnabled()
    w.experience_panel.stop_button.click()
    until(lambda: w.worker is None, timeout=1)
    assert not runner.results and w.send_button.isEnabled()


def test_stale_can_be_retested_without_reusing_old_evidence(tmp_path):
    store = ExperienceStore(tmp_path/'experience.db')
    fp = register(store, profile('a'))
    populate(store, fp)
    store.mark_stale(fp)
    store.start_measurement(fp)
    store.record(fp, C.PYTHON_DEBUG, S.VERIFIED_FAILURE)
    row = store.summary(fp, C.PYTHON_DEBUG)
    assert row['sample_count'] == row['hard_sample_count'] == 1
    assert row['success_rate'] == 0
    assert any(r['stale'] and r['sample_count'] == 10 for r in store.summaries())


def test_native_tool_evidence_does_not_claim_analysis_is_correct(tmp_path):
    from test_routed_agent import setup
    from alienape.backends import tool_reply
    settings, registry, pool = setup(tmp_path, {'coder': [tool_reply('read_file', {'path':'player.py'}), answer('Solved!')]}, coding_mode='native')
    store = ExperienceStore(tmp_path/'experience.db')
    agent = RoutedAgent(Workspace(tmp_path), settings, registry=registry, pool=pool, experience=store)
    asyncio.run(agent.run('Debug player.py'))
    rows = {r['task_category']:r for r in store.summaries()}
    assert rows['PYTHON_DEBUG']['hard_sample_count'] == 0
    assert rows['TOOL_USE']['verified_success_count'] == 1


def test_measured_tokens_are_kept_and_answers_are_absent(tmp_path):
    class Metered(RecordingMock):
        async def chat(self, messages, tools=None, model=''):
            response = await super().chat(messages, tools, model)
            self.last_usage = {'prompt_tokens': 111, 'completion_tokens': 22}
            return response
    settings, registry, store, _, _, _ = routed_store(tmp_path)
    models = {p.id: Metered([answer('private-answer-marker')]) for p in settings.models}
    settings.models[0].capabilities.append("general")  # General explanation fixture declares eligibility.
    agent = RoutedAgent(Workspace(tmp_path), settings, registry=registry, pool=Pool(models), experience=store)
    asyncio.run(agent.run('Explain Python'))
    row = store.summaries()[0]
    assert row['median_input_tokens'] == 111 and row['median_output_tokens'] == 22
    assert not row['tokens_estimated']
    assert b'private-answer-marker' not in store.database.read_bytes()


def test_config_new_fields_round_trip_and_benchmarks_never_auto(tmp_path):
    s = Settings()
    s.routing.min_samples_for_experience = 7
    s.routing.quality[str(C.REASONING)] = .95
    path = tmp_path/'config.toml'
    s.save(path)
    assert Settings.load(path) == s
    s.benchmark.auto_run = True
    with pytest.raises(ValueError):
        s.validate()
