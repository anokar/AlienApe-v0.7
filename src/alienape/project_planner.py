"""One bounded, tool-free planner call using the existing model router."""
import json
import time

from .context import size
from .project_plan import MAX_FILES, MAX_DIRECTORIES, MAX_MUTATIONS, MAX_PATH_DEPTH
from .security import transport_redactor
from .validators import JSONValidator


class PlannerUnavailable(Exception):
    pass


class PlannerProtocolFailure(Exception):
    pass


async def propose_plan(agent, request):
    if not hasattr(agent, 'router') or not hasattr(agent, 'pool'):
        raise PlannerUnavailable('No configured planner router')
    try:
        route=agent.router.select_model('Plan a small project', state={'manual_model': agent.manual_model}, task_capability='reasoning', protocol='structured_json')
    except ValueError as exc:
        raise PlannerUnavailable('No eligible structured planning model') from exc
    memories=[]
    if agent.memory and agent.settings.memory.enabled:
        records=agent.memory.retrieve(request.goal, min(agent.settings.memory.retrieval_limit, 5))
        memories=[{'key':m['logical_key'],'content':m['content'][:1000]} for m in records if m.get('sensitivity')=='SAFE']
    packet={'goal':request.goal[:4000],'project_label':request.root,'language_hint':request.language_hint,'relevant_memory':memories,
            'limits':{'max_files':MAX_FILES,'max_directories':MAX_DIRECTORIES,'max_mutations':MAX_MUTATIONS,'max_path_depth':MAX_PATH_DEPTH},
            'schema':{'files':[{'path':'relative/path','purpose':'brief purpose','language':'language','depends_on':['relative/path']}]}}
    messages=transport_redactor.clean([{'role':'system','content':'Propose only a small project plan. Return one JSON object following the exact schema; no Markdown, prose, extra fields or actions. All file paths must be relative. No tools.'},{'role':'user','content':json.dumps(packet,ensure_ascii=False)}])
    if size(messages)>16000:raise PlannerUnavailable('Planning context exceeds limit')
    agent.check_stop()
    if agent.resources.counters.model_calls>=agent.model_call_limit:raise PlannerUnavailable('Model call budget exhausted')
    agent.resources.counters.model_calls+=1
    agent.emit('PROJECT_PLANNER_CONTEXT',{'model':route.profile.id,'context_bytes':size(messages),'memory_selected':len(memories),'memory_context_bytes':len(json.dumps(memories,ensure_ascii=False).encode('utf-8'))})
    backend=agent.pool.get(route.profile)
    started=time.monotonic()
    with backend.output_budget(agent.settings.resources.output.default_limit):
        response=await backend.chat(messages,tools=[],model=route.profile.model_name)
    agent.check_stop()
    agent.emit('PROJECT_PLANNER_CALL',{'model':route.profile.id,'protocol':'structured_json','elapsed_ms':round((time.monotonic()-started)*1000,2),'context_bytes':size(messages)})
    if not isinstance(response,dict) or response.get('tool_calls') or not JSONValidator().validate('',response).valid:
        raise PlannerProtocolFailure('Invalid planner structured response')
    raw=response.get('content')
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>32768:
        raise PlannerProtocolFailure('Invalid planner response size')
    return raw,route.profile.id
