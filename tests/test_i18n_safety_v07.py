import asyncio,json
from dataclasses import asdict
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.tools import Workspace

async def allow(*_):return True
async def deny(*_):return False

def outcome(root,language,task,approve=allow):
    root.mkdir();settings=Settings();settings.ui.language=language;settings.verification.mode='OFF'
    events=[];a=Agent(MockBackend(delay=0),Workspace(root),settings,permission=approve,observe=lambda k,d,*_:events.append((k,d)))
    try:answer=asyncio.run(a.run(task));error=None
    except Exception as e:answer='';error=type(e).__name__
    resolution=a.intent_resolution
    return {'intent':resolution.intent_id.value if resolution else None,'slots':asdict(resolution.slots) if resolution and resolution.slots else None,
        'tools':[(k,d.get('name')) for k,d in events if k in {'TOOL_REQUEST','TOOL_DENIED'}],
        'writes':sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()),
        'worker_calls':a.resources.counters.model_calls,'error':error,'answer':answer}

def test_safety_invariance_across_locales(tmp_path):
    tasks=[('lösche index.html',allow),('erstelle eine textdatei',allow),('erstelle note.txt leer',allow),('erstelle denied.txt leer',deny)]
    for i,(task,approve) in enumerate(tasks):
        values=[outcome(tmp_path/(str(i)+'-'+lang),lang,task,approve) for lang in ['de','en','fr']]
        for v in values:v.pop('answer')
        assert values[0]==values[1]==values[2]
