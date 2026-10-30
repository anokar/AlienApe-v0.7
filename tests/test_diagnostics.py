"""Trace probes must explain behavior without changing it; all stores isolated."""
import asyncio,copy,hashlib,json,uuid
from zipfile import ZipFile
import httpx,pytest,jsonschema
from alienape.agent import Agent,Session,ToolDenied
from alienape.backends import MockBackend,OllamaBackend,tool_reply
from alienape.config import Settings
from alienape.diagnostics import DiagnosticStore,SCHEMA,active_trace,sanitize
from alienape.security import redactor
from alienape.tools import Workspace
from alienape.completion import CompletionRejected
from alienape.experience import ExperienceStore
from test_workspace_discovery import make
from test_completion_recovery import response
from test_routed_agent import Pool


def enable(agent,tmp_path):
 agent.settings.diagnostic_trace=True;agent.diagnostic_store=DiagnosticStore(tmp_path/'diag');return agent.diagnostic_store


def bundle(store,tmp_path,**kw):
 path=store.export(tmp_path/(uuid.uuid4().hex+'.zip'),**kw)
 with ZipFile(path) as z:
  assert set(z.namelist())=={'CHAT.md','TRACE.jsonl','SUMMARY.json','MANIFEST.json'}
  traces=[json.loads(line) for line in z.read('TRACE.jsonl').decode().splitlines()]
  for trace in traces:jsonschema.validate(trace,SCHEMA)
  return traces,z.read('CHAT.md').decode(),json.loads(z.read('SUMMARY.json')),json.loads(z.read('MANIFEST.json'))


def test_disabled_is_lazy_and_does_not_call_sink(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=DiagnosticStore(tmp_path/'diag')
 store.begin=lambda *_:pytest.fail('Disabled trace must not begin');a.diagnostic_store=store
 assert not a.settings.diagnostic_trace
 assert 'AlienApe' in asyncio.run(a.run('hallo'))
 assert not (tmp_path/'diag').exists() and a.diagnostic_trace is None and active_trace.get() is None


def test_config_default_roundtrip_and_type(tmp_path):
 s=Settings();assert not s.diagnostic_trace
 s.diagnostic_trace=True;s.save(tmp_path/'config.toml');assert Settings.load(tmp_path/'config.toml').diagnostic_trace
 s.diagnostic_trace='yes'
 with pytest.raises(ValueError):s.validate()


def test_direct_create_has_stable_ids_and_zero_model_usage(tmp_path):
 async def yes(*_):return True
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=yes);store=enable(a,tmp_path)
 answer=asyncio.run(a.run('erstelle hello.txt mit dem Text "Hallo Welt"'))
 traces,chat,summary,manifest=bundle(store,tmp_path)
 t=traces[0];assert t['trace_id'] in chat and t['input']['trace_id']==t['trace_id']
 assert {e['trace_id'] for e in t['events']}=={t['trace_id']}
 assert t['intent']['content_mode']=='LITERAL' and t['intent']['literal_content']=='Hallo Welt'
 assert t['final_response']['text']==answer and not t['model_requests']
 assert t['resources']['turn_delta']['model_calls']==0 and t['resources']['actual_input_tokens']==0
 assert not t['experience']['produced'] and summary['actual_dispatches']==1 and manifest['redaction_enabled']
 assert (tmp_path/'hello.txt').read_text()=='Hallo Welt'


def test_ambiguity_is_observed_without_being_repaired(tmp_path):
 task="erstelle helloworld.py mit 'unvollständigem Text"
 a,b=make(tmp_path);store=enable(a,tmp_path);answer=asyncio.run(a.run(task))
 t=bundle(store,tmp_path)[0][0]
 assert a.intent_resolution.ambiguity_reason and answer==a.intent_resolution.ambiguity_reason
 assert t['intent']['intent_id']=='CREATE_FILE' and t['intent']['content_mode']=='UNKNOWN'
 assert t['intent']['lexical_path_candidates']==['helloworld.py']
 assert not b.requests and not t['model_requests'] and not t['routing'] and not t['tools']['dispatches']


def test_model_route_completions_and_experience_are_recorded(tmp_path):
 events=[];store_db=ExperienceStore(tmp_path/'exp.db')
 a,b=make(tmp_path,responses=[response('print("Hello World")')],store=store_db,events=events)
 store=enable(a,tmp_path);answer=asyncio.run(a.run('schreib mir hallo welt in python'))
 t=bundle(store,tmp_path)[0][0]
 assert t['routing'][0]['reason'] and t['routing'][0]['declared_capabilities']
 assert t['routing'][0]['fingerprint'] and t['experience']['produced'] and t['experience']['records']
 assert t['model_requests'][0]['message_count']>0 and t['model_completions']
 assert t['final_response']['text']==answer and t['resources']['turn_delta']['model_calls']==1
 assert t['validation'] and t['task']['response_contract']


def test_21_calls_are_parsed_but_none_dispatched(tmp_path):
 calls=[]
 for n in range(21):
  c=tool_reply('write_file',{'path':'hello.py','content':'print(1)'})['tool_calls'][0];c['id']='c'+str(n);calls.append(c)
 a,b=make(tmp_path,'native',[response('',calls=calls)]);store=enable(a,tmp_path)
 with pytest.raises(CompletionRejected,match='maximal 8'):asyncio.run(a.run('erstelle hello.py'))
 t,_,summary,_=bundle(store,tmp_path);t=t[0]
 assert len(t['tools']['parsed'])==21 and all(c['parse_valid'] for c in t['tools']['parsed'])
 assert len([g for g in t['tools']['guards'] if g.get('reason')=='MAX_TOOL_CALLS'])==21
 assert summary['model_requested_tool_calls']==21 and summary['actual_dispatches']==0
 assert not t['tools']['requests'] and t['resources']['turn_delta']['file_writes']==0
 assert t['final_response']['failure_classification']=='TOOL_FAILURE'


def test_permission_wait_has_request_without_write(tmp_path):
 async def run():
  gate=asyncio.Event()
  async def permission(*_):gate.set();await asyncio.sleep(100)
  a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=permission);store=enable(a,tmp_path)
  work=asyncio.create_task(a.run('erstelle hello.txt mit "Hello"'))
  await asyncio.wait_for(gate.wait(),2)
  t=store.records()[0]
  assert len(t['tools']['requests'])==1 and not t['tools']['dispatches']
  assert any(e['data'].get('details',{}).get('status')=='Warte auf Erlaubnis' for e in t['events'])
  assert not (tmp_path/'hello.txt').exists();work.cancel()
  with pytest.raises(asyncio.CancelledError):await work
 asyncio.run(run())


def test_controller_exception_is_not_model_failure(tmp_path):
 async def yes(*_):return True
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=yes);store=enable(a,tmp_path)
 def broken(*_):raise RuntimeError('controller fixture failure')
 a.tools.execute=broken
 with pytest.raises(RuntimeError):asyncio.run(a.run('erstelle hello.txt mit "Hello"'))
 t=bundle(store,tmp_path)[0][0]
 assert t['final_response']['failure_origin']=='CONTROLLER' and not t['experience']['produced']
 assert not t['model_completions'] and not t['routing'] and t['tools']['results'][0]['status']=='ERROR'


def test_dispatches_and_no_progress_are_distinct(tmp_path):
 calls=[]
 for n in range(8):
  c=tool_reply('not_a_tool',{})['tool_calls'][0];c['id']='c'+str(n);calls.append(c)
 a,b=make(tmp_path,'native',[response('',calls=calls)]);store=enable(a,tmp_path)
 with pytest.raises(CompletionRejected,match='NO_PROGRESS'):asyncio.run(a.run('Nutze Tools im Workspace'))
 t=bundle(store,tmp_path)[0][0]
 assert len(t['tools']['parsed'])==8 and len(t['tools']['requests'])==2 and not t['tools']['dispatches']
 assert any(g.get('subreason')=='NO_PROGRESS' for g in t['tools']['guards'])


def test_canary_redacted_on_disk_and_export(tmp_path):
 canary='DIAG_CANARY_NOT_REAL_'+uuid.uuid4().hex;redactor.register(canary)
 a,b=make(tmp_path,responses=[response('A '+canary+' Authorization: Bearer '+canary)])
 store=enable(a,tmp_path);asyncio.run(a.run('erkläre mir ein Python Beispiel '+canary))
 path=store.export(tmp_path/'canary.zip')
 with ZipFile(path) as z:assert all(canary.encode() not in z.read(n) for n in z.namelist())
 assert all(canary.encode() not in p.read_bytes() for p in (tmp_path/'diag').rglob('*.jsonl'))


def test_sensitive_file_is_structure_or_metadata_only(tmp_path):
 unknown='UNKNOWN_PRIVATE_FILE_VALUE_'+uuid.uuid4().hex
 (tmp_path/'.env').write_text('ODD_VARIABLE='+unknown)
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=enable(a,tmp_path)
 asyncio.run(a.run('Lies ".env"'))
 t=bundle(store,tmp_path)[0][0]
 assert unknown not in json.dumps(t) and unknown not in a.diagnostic_trace.path.read_text(encoding='utf-8')
 a.diagnostic_trace.record('TEST',{'messages':[{'content':json.dumps({'files':[{'path':'.env','content':unknown}]})}]})
 assert unknown not in a.diagnostic_trace.path.read_text(encoding='utf-8')


def test_wire_capture_has_no_headers_and_preserves_protocol_context(tmp_path,monkeypatch):
 canary='HTTP_CANARY_NOT_REAL_'+uuid.uuid4().hex;monkeypatch.setenv('ALIENAPE_OLLAMA_API_KEY',canary)
 sent=[]
 def handler(request):
  assert request.headers['Authorization']=='Bearer '+canary
  sent.append(json.loads(request.content))
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'print(1)','reasoning_content':canary}}],'usage':{'prompt_tokens':12,'completion_tokens':4}})
 a,b=make(tmp_path);a.pool=Pool({'test':OllamaBackend(transport=httpx.MockTransport(handler))});store=enable(a,tmp_path)
 asyncio.run(a.run('schreib mir hallo welt in python'));t=bundle(store,tmp_path)[0][0]
 wire=[r for r in t['model_requests'] if r['stage']=='WIRE_REQUEST'];assert wire and 'temperature' in wire[0]
 assert 'headers' not in wire[0] and canary not in json.dumps(t) and 'reasoning_content' not in json.dumps(t)
 assert t['resources']['actual_input_tokens']==12 and t['resources']['actual_output_tokens']==4


def test_bounds_redact_before_truncation_and_mark_size():
 secret='BOUND_CANARY_'+uuid.uuid4().hex;redactor.register(secret)
 text='x'*16370+secret+'y'*20000
 value=sanitize(text);assert value['truncated'] and value['original_size']>16384
 assert secret not in value['text'] and len(value['text'].encode())<=16384


def test_long_trace_still_has_final_resources(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=enable(a,tmp_path)
 asyncio.run(a.run('hallo'));trace=a.diagnostic_trace
 for n in range(600):trace.record('TEST',{'n':n})
 trace.finish(a,'done');t=bundle(store,tmp_path)[0][0]
 assert t['truncated'] and t['resources']['turn_delta']['model_calls']==0 and t['final_response']['text']=='done'
 assert trace.path.stat().st_size<2*1024*1024


def test_last_task_export_is_only_last_trace(tmp_path):
 store=DiagnosticStore(tmp_path/'diag')
 for text in ['hallo','wer bist du?']:
  a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.settings.diagnostic_trace=True;a.diagnostic_store=store;asyncio.run(a.run(text))
 all_rows=bundle(store,tmp_path)[0];last,chat,summary,_=bundle(store,tmp_path,last_only=True)
 assert len(all_rows)==2 and len(last)==1 and last[0]['input']['user_text']=='wer bist du?'
 assert summary['trace_ids']==[last[0]['trace_id']] and all_rows[0]['trace_id'] not in chat


def test_broken_diagnostic_storage_does_not_change_product_result(tmp_path,monkeypatch):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=enable(a,tmp_path)
 def broken(*_):raise OSError('mock diagnostic I/O unavailable')
 store.begin=broken
 # Real begin catches errors; simulate failure inside its file creation path instead.
 store.begin=DiagnosticStore.begin.__get__(store);monkeypatch.setattr('alienape.diagnostics.DiagnosticTrace',broken)
 assert 'AlienApe' in asyncio.run(a.run('hallo')) and store.last_error
 assert active_trace.get() is None


def test_on_off_coding_outcome_and_experience_match(tmp_path):
 results=[]
 for flag in [False,True]:
  folder=tmp_path/str(flag);folder.mkdir();store_db=ExperienceStore(folder/'exp.db')
  a,b=make(folder,'none',[response('print(1)')],store=store_db);a.settings.diagnostic_trace=flag;a.diagnostic_store=DiagnosticStore(folder/'diag')
  answer=asyncio.run(a.run('schreib mir hallo welt in python'))
  with store_db.connection() as db:rows=[tuple(r) for r in db.execute('SELECT task_category,success_signal,failure_type,model_calls FROM model_experience')]
  results.append((answer,len(b.requests),a.resources.counters.model_calls,rows))
 assert results[0]==results[1]


def test_separate_tasks_use_separate_context_ids(tmp_path):
 async def run():
  store=DiagnosticStore(tmp_path/'diag')
  agents=[]
  for text in ['hallo','danke']:
   a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.settings.diagnostic_trace=True;a.diagnostic_store=store;agents.append(a)
  await asyncio.gather(*(a.run(text) for a,text in zip(agents,['hallo','danke'])))
  assert len({a.diagnostic_trace.trace_id for a in agents})==2 and active_trace.get() is None
 asyncio.run(run())


def test_unknown_header_values_and_private_keys_never_persist(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=enable(a,tmp_path);asyncio.run(a.run('hallo'))
 hidden='UNKNOWN_HEADER_VALUE_'+uuid.uuid4().hex
 a.diagnostic_trace.wire_request('mock',{'headers':{'Authorization':hidden},'messages':[{'role':'system','content':json.dumps({'headers':{'X-Custom':hidden},'private_key':'-----BEGIN PRIVATE KEY-----\nOTHER_SECRET_DATA\n-----END PRIVATE KEY-----'})}]})
 text=a.diagnostic_trace.path.read_text(encoding='utf-8');assert hidden not in text and 'OTHER_SECRET_DATA' not in text
 t=bundle(store,tmp_path)[0][0];assert hidden not in json.dumps(t)


def test_dispatch_error_does_not_change_exception_identity(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=enable(a,tmp_path);asyncio.run(a.run('hallo'))
 expected=ValueError('same tool exception')
 def fail(*_):raise expected
 with pytest.raises(ValueError) as raised:a.diagnostic_trace.dispatch(fail,'read_file',{'path':'a.txt'})
 assert raised.value is expected


def test_file_metadata_is_preferred_over_duplicate_content(tmp_path):
 (tmp_path/'hello.txt').write_text('CONTENTS_NEED_NOT_BE_DUPLICATED')
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=enable(a,tmp_path)
 asyncio.run(a.run('Lies hello.txt'));t=bundle(store,tmp_path)[0][0]
 result=t['tools']['results'][0]['result']
 assert result['content']['omitted'] and result['content']['sha256'] and result['content']['bytes']>0
 assert t['final_response']['source']=='TOOL_RESULT'


def test_ui_debug_toggle_and_exports(window,monkeypatch,tmp_path):
 from alienape.dialogs import SettingsDialog
 from PyQt6.QtWidgets import QDialog
 from test_gui import send
 dialog=SettingsDialog(window.settings,window)
 try:
  assert not dialog.diagnostic_trace.isChecked();dialog.diagnostic_trace.setChecked(True)
  dialog.save();assert dialog.result()==QDialog.DialogCode.Accepted and dialog.settings.diagnostic_trace
 finally:dialog.close()
 window.settings.diagnostic_trace=True;send(window,'hallo');send(window,'wer bist du?')
 destination=tmp_path/'diagnostic_bundle_test.zip'
 monkeypatch.setattr('alienape.ui.QFileDialog.getSaveFileName',lambda *_:(str(destination),'ZIP (*.zip)'))
 window.technical.setChecked(True);window.export_last_diagnostic.click()
 with ZipFile(destination) as z:
  records=z.read('TRACE.jsonl').decode().splitlines();assert len(records)==1
  trace=json.loads(records[0]);assert trace['input']['user_text']=='wer bist du?'
 events=[json.loads(x) for x in (window.directory/'logs/events.jsonl').read_text(encoding='utf-8').splitlines()]
 tagged=[e for e in events if e.get('diagnostic_trace_id')==trace['trace_id']]
 assert tagged and all(e['diagnostic_trace_id']==trace['trace_id'] for e in tagged)


def test_ui_worker_error_handler_final_text(window):
 from test_gui import send
 window.settings.diagnostic_trace=True
 send(window,'erstelle ../../outside.txt mit "no"')
 records=window.diagnostics.records(window.event_log.session_id,last_only=True)
 assert records[0]['final_response']['source']=='ERROR_HANDLER'
 assert records[0]['final_response']['text'] in window.chat.toPlainText()


from test_gui import window,app


def test_worker_without_trace_interface_keeps_error_handling(app):
 import threading
 from alienape.worker import AgentWorker
 class ExistingRunner:
  stopped=threading.Event()
  async def run(self,_):raise ValueError('existing runner error')
 worker=AgentWorker(agent=ExistingRunner(),task='fixture');failures=[];worker.failure.connect(failures.append)
 worker.run();assert failures==['existing runner error']


def test_export_preflight_error_stays_inside_debug_ui(window,monkeypatch):
 messages=[]
 monkeypatch.setattr(window.diagnostics,'records',lambda *_:(_ for _ in ()).throw(OSError('diagnostic read failure')))
 monkeypatch.setattr('alienape.ui.QMessageBox.warning',lambda *args:messages.append(args[-1]))
 window.export_diagnostics(True);assert messages==['diagnostic read failure']


def test_store_quota_stops_only_tracing(tmp_path,monkeypatch):
 store=DiagnosticStore(tmp_path/'diag')
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.settings.diagnostic_trace=True;a.diagnostic_store=store
 asyncio.run(a.run('hallo'));monkeypatch.setattr('alienape.diagnostics.STORE_LIMIT',1)
 b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());b.settings.diagnostic_trace=True;b.diagnostic_store=store
 assert 'AlienApe' in asyncio.run(b.run('hallo')) and b.diagnostic_trace is None
 assert store.last_error and len(store.records())==1
