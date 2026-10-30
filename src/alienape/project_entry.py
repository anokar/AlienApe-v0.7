"""Entry adapter for the deliberately small new-project builder."""
from dataclasses import replace
import json
import re
import time

from .i18n import localize
from .intent_resolution import IntentID
from .project_request import project_request
from .project_plan import InvalidProjectPlan, validate_plan
from .project_orchestrator import execute_project, summarize_project
from .security import SensitiveFilePolicy


def deterministic_plan(request):
    """Only known simple shapes; unsupported goals are not silently reduced."""
    goal=request.goal.casefold()
    if request.language_hint=='python' and re.search(r'\b(?:test|tests)\b',goal) and re.search(r'\b(?:addier\w*|addition|add\w*|summ\w*)\b',goal) and re.search(r'\b(?:zahl\w*|numbers?)\b',goal):
        files=[{'path':'src/calculator.py','purpose':'Implement the requested addition of two numbers.','language':'python','depends_on':[]}, {'path':'tests/test_calculator.py','purpose':'Test the addition module using its actual interface.','language':'python','depends_on':['src/calculator.py']}]
    elif request.language_hint=='html' and all(re.search(p,goal) for p in (r'\bhtml\b',r'\bcss\b',r'\b(?:javascript|js)\b')):
        files=[{'path':'index.html','purpose':'Create the requested static website and reference style.css and app.js.','language':'html','depends_on':[]},{'path':'style.css','purpose':'Style the requested website.','language':'css','depends_on':['index.html']},{'path':'app.js','purpose':'Provide the requested website behavior.','language':'javascript','depends_on':['index.html']}]
    else:
        return None
    return json.dumps({'files':files},ensure_ascii=False)


def safe_root(agent,root):
    if not isinstance(root,str) or not re.fullmatch(r'[\w-]{1,80}',root):
        return False
    try:
        SensitiveFilePolicy.writable(root)
        path=agent.workspace.path(root)
        return path.parent==agent.workspace.root
    except (ValueError,PermissionError,OSError):
        return False


async def maybe_project_build(agent,task):
    """Return (handled, response); pending state stays in the current session."""
    pending=agent.session.pending_project
    agent.session.pending_project=None
    request=project_request(task)
    if pending and time.monotonic()-pending[1]<=300 and pending[2]==str(agent.workspace.root):
        if request is None and re.fullmatch(r'[\w-]{1,80}',task.strip()):
            request=replace(pending[0],root=task.strip())
    if request is None:
        return False,None
    lang=agent.settings.ui.language
    agent.emit('PROJECT_BUILD_REQUEST',{'intent':'PROJECT_BUILD'})
    agent.intent_resolution=agent.intent_resolver.resolve(request.goal)
    if agent.intent_resolution.intent_id!=IntentID.PROJECT_BUILD:
        return True,localize('PROJECT_UNAVAILABLE',lang)
    if not request.root:
        agent.session.pending_project=(request,time.monotonic(),str(agent.workspace.root))
        agent.emit('PROJECT_ROOT_PENDING',{})
        return True,localize('PROJECT_NAME_REQUIRED',lang)
    if not safe_root(agent,request.root):
        return True,localize('PROJECT_ROOT_INVALID',lang)
    agent.emit('PROJECT_ROOT_RESOLVED',{'root':request.root})
    slots=replace(agent.intent_resolution.slots,project_root=request.root,missing_slots=())
    agent.intent_resolution=replace(agent.intent_resolution,slots=slots)
    raw=deterministic_plan(request)
    source='DETERMINISTIC' if raw is not None else 'MODEL'
    agent.emit('PROJECT_PLAN_REQUEST',{'source':source})
    if raw is None:
        from .project_planner import propose_plan, PlannerUnavailable, PlannerProtocolFailure
        try:
            raw,planner=await propose_plan(agent,request)
        except PlannerUnavailable:
            agent.emit('PROJECT_PLAN_REJECTED',{'source':'ROUTING','attribution':'CAPABILITY_UNAVAILABLE'})
            return True,localize('PROJECT_UNAVAILABLE',lang)
        except PlannerProtocolFailure:
            agent.emit('PROJECT_PLAN_REJECTED',{'source':'MODEL','attribution':'PROJECT_PLAN_PROTOCOL'})
            return True,localize('PROJECT_PLAN_INVALID',lang)
        except Exception:
            agent.emit('PROJECT_PLAN_REJECTED',{'source':'RESOURCE','attribution':'PLANNER_CALL_FAILURE'})
            return True,localize('PROJECT_UNAVAILABLE',lang)
    agent.emit('PROJECT_PLAN_RECEIVED',{'source':source})
    try:
        validate_plan(raw,agent.workspace,request.root,request.goal)
        agent.emit('PROJECT_PLAN_VALIDATED',{'source':source})
        agent.emit('PROJECT_EXECUTION_START',{'root':request.root})
        resolution=agent.intent_resolution
        result=await execute_project(agent,raw,request.root,request.goal)
        agent.intent_resolution=resolution
        result.plan_source=source
        agent.project_build_result=result
        agent.emit('PROJECT_BUILD_COMPLETE',{'status':result.project_status,'root':request.root})
        return True,summarize_project(result,lang)
    except FileExistsError:
        return True,localize('PROJECT_ROOT_EXISTS',lang)
    except InvalidProjectPlan:
        agent.emit('PROJECT_PLAN_REJECTED',{'source':'MODEL' if source=='MODEL' else 'CONTROLLER','attribution':'PLAN_PROPOSAL' if source=='MODEL' else 'VALIDATOR'})
        return True,localize('PROJECT_PLAN_INVALID',lang)
    except (ValueError,PermissionError,OSError):
        agent.emit('PROJECT_BUILD_COMPLETE',{'status':'INTERRUPTED','root':request.root})
        return True,localize('PROJECT_INTERRUPTED',lang)
