"""Small versioned router training, using the existing completion/permission boundary."""
from dataclasses import dataclass,asdict,replace
from pathlib import Path
import asyncio,csv,json,threading,time,uuid,tempfile
from .security import redactor
from .agent import Agent
from .backends import MockBackend,tool_reply
from .completion import FailureType as F,CompletionRejected
from .contracts import ResponseContract,ResponseMode as M
from .config import Settings
from .context import size
from .experience import ModelFingerprint,SuccessSignal as S,SUITE_VERSION
from .routing import BackendPool
from .task_packet import TaskPacket
from .tools import Workspace,ToolArgumentInvalid
from .validators import ResultValidator,ValidationResult,ExactValueValidator,JSONValidator

DOMAINS=('GENERAL_SIMPLE','PYTHON_CODING','PYTHON_DEBUG','CODE_REVIEW','GODOT_CODE','FILE_ANALYSIS','TOOL_USE','REASONING')
GROUPS={'GENERAL_SIMPLE':'general','PYTHON_CODING':'coding','PYTHON_DEBUG':'coding','CODE_REVIEW':'coding',
        'GODOT_CODE':'coding','FILE_ANALYSIS':'files','TOOL_USE':'tools','REASONING':'reasoning','PLANNING':'reasoning','SUMMARIZATION':'general'}

@dataclass(frozen=True)
class TrainingTask:
    id:str
    category:str
    prompt:str
    validator:str
    expected:object
    response_contract:str='PLAIN_TEXT'
    difficulty:str='EASY'
    fixtures:tuple=()

    @property
    def task_domain(self):return self.category


def catalog():
    zones='enum ZoneType { SAFE, WILDERNESS, ARENA }\n'
    rules=zones+'static func can_attack(a, b):\n    if a == ZoneType.SAFE or b == ZoneType.SAFE:\n        return false\n    return a == b\n'
    def t(key,domain,prompt,validator,expected,contract='PLAIN_TEXT',difficulty='EASY',fixtures=()):
        return TrainingTask(f'v{SUITE_VERSION}-{key}',domain,prompt,validator,expected,contract,difficulty,fixtures)
    return [
        t('general-add','GENERAL_SIMPLE','Was ist 7 + 5? Antworte nur mit der Zahl.','exact','12'),
        t('general-fact','GENERAL_SIMPLE','Ada hat 4 rote und 3 blaue Würfel. Wie viele Würfel insgesamt? Nur die Zahl.','exact','7',difficulty='NORMAL'),
        t('json-name','GENERAL_SIMPLE','Antworte nur als JSON: name ist Ada, count ist die Ganzzahl 3. Genau diese zwei Schlüssel.','json',{'name':'Ada','count':3},'STRUCTURED_JSON'),
        t('json-types','GENERAL_SIMPLE','Antworte nur als JSON mit city gleich Bern und active gleich true. Genau diese zwei Schlüssel.','json',{'city':'Bern','active':True},'STRUCTURED_JSON','NORMAL'),
        t('python-add','PYTHON_CODING','Write Python function solve(a,b) returning the addition of a and b. Return only Python code; no imports or external calls.','python','+'),
        t('python-debug','PYTHON_DEBUG','Fix this Python bug: def solve(a,b): return a*b. Required behavior: subtraction of a and b. Return only Python code; no imports or external calls.','python','-','PLAIN_TEXT','NORMAL'),
        t('godot-enum','GODOT_CODE','Analysiere zones.gd. Antworte nur als JSON mit zones und den Enum-Namen in deklarierter Reihenfolge.','json',{'zones':['SAFE','WILDERNESS','ARENA']},'STRUCTURED_JSON','EASY',(('zones.gd',zones),)),
        t('review-divide','CODE_REVIEW','Review Python: def divide(a,b): return a*b. Required behavior is a divided by b. Return only the correct operator character.','exact','/','PLAIN_TEXT','NORMAL'),
        t('godot-rule','GODOT_CODE','Analysiere rules.gd: can_attack(ARENA, WILDERNESS). Antworte nur als JSON mit allowed.','json',{'allowed':False},'STRUCTURED_JSON','NORMAL',(('rules.gd',rules),)),
        t('godot-boundary','GODOT_CODE','Analysiere limit.gd für n=5. Antworte nur als JSON mit accepted.','json',{'accepted':False},'STRUCTURED_JSON','HARD',(('limit.gd','static func accepted(n):\n    return n > 5 and n < 10\n'),)),
        t('file-owner','FILE_ANALYSIS','Analysiere settings.cfg. Antworte nur als JSON mit owner und items aus der Datei.','json',{'owner':'Ada','items':7},'STRUCTURED_JSON','EASY',(('settings.cfg','owner=Ada\nitems=7\n'),)),
        t('file-proposal','FILE_ANALYSIS','Ändere nur note.txt: der vollständige Inhalt soll READY ohne Zeilenumbruch sein. Gib einen FILE_PROPOSAL zurück, keine Ausführung.','proposal',{'path':'note.txt','content':'READY'},'FILE_PROPOSAL','NORMAL',(('note.txt','OLD'),)),
        t('tool-read','TOOL_USE','Rufe read_file mit genau path=fixture.txt auf. Keine Textantwort.','tool',{'name':'read_file','arguments':{'path':'fixture.txt'}},'TOOL_ACTION','EASY',(('fixture.txt','the token is COBALT\n'),)),
        t('tool-search','TOOL_USE','Rufe search_files mit genau directory=. und query=COBALT auf. Keine Textantwort.','tool',{'name':'search_files','arguments':{'directory':'.','query':'COBALT'}},'TOOL_ACTION','NORMAL',(('fixture.txt','red\nCOBALT\nblue\n'),)),
        t('reason-order','REASONING','Dependencies: A before B; B before C. Return only the ordered IDs separated by commas.','exact','A,B,C','PLAIN_TEXT','NORMAL'),
        t('reason-cost','REASONING','A-B kostet 2, B-C kostet 3, A-C kostet 9. Was ist der kleinste Wegpreis A nach C? Antworte nur als JSON mit cost.','json',{'cost':5},'STRUCTURED_JSON','HARD'),
    ]


def training_suite(categories=None):
    selected=set(map(str,categories or DOMAINS))
    if 'STRUCTURED_OUTPUT' in selected:selected.add('GENERAL_SIMPLE')
    if 'PLANNING' in selected:selected.add('REASONING')
    return [t for t in catalog() if t.category in selected]


def declared(profile,group):
    caps=set(profile.capabilities)
    return {'general':bool(caps&{'general','chat'}),'coding':bool(caps&{'coding','debugging','code_review'}),
        'reasoning':bool(caps&{'planning','deep_reasoning','reasoning'}),'tools':bool(caps&{'tool_use','tools'}),
        # Files are an existing shared controller facility for every configured profile.
        'files':True}[group]


def plan_for(profile,tasks,scope='quick'):
    scope={'short':'quick','normal':'standard'}.get(scope,scope)
    if scope=='project_plan':return list(tasks)
    if scope=='file_transport':
        required='file_content_python' if profile.tool_mode=='none' else 'native_write_python' if profile.tool_mode=='native' else None
        return [t for t in tasks if t.validator==required]
    if scope in {'lite','medium','hard'}:
        from .onboarding import plan_v2
        return plan_v2(profile,tasks,scope)
    if scope not in {'quick','standard'}:raise ValueError('Nur QUICK oder STANDARD erlaubt.')
    buckets={group:[] for group in ['general','coding','files','tools','reasoning']}
    for task in tasks:
        group=GROUPS.get(str(task.category),'general')
        if group=='tools' and profile.tool_mode=='none':continue
        buckets[group].append(task)
    picked=[]
    for group,items in buckets.items():
        maximum=(3 if group=='coding' else 4 if group=='general' else 2) if scope=='quick' else 16
        if not declared(profile,group):maximum=1
        picked.extend(items[:maximum])
    # Stable order across models; no random resampling or synthetic sample padding.
    ordered=[t for t in tasks if t in picked]
    return ordered[:8 if scope=='quick' else 16]


class TrainingValidator(ResultValidator):
    def __init__(self,task,workspace=None):self.task,self.workspace=task,workspace
    def validate(self,task,response):
        item=self.task
        if item.validator=='json':return ExactValueValidator(item.expected).validate(task,response)
        if item.validator=='file_content_python':
            import ast
            from .benchmark import check_python
            proposals=getattr(response,'proposed_files',[])
            try:
                okay=len(proposals)==1
                if okay:ast.parse(proposals[0]['content']);okay=check_python(proposals[0]['content'],item.expected['symbol'])
            except (SyntaxError,ValueError,ZeroDivisionError,OverflowError):okay=False
            return ValidationResult(okay,True,True,F.SUCCESS if okay else F.VALIDATION_FAILURE,{},'task_quality')
        if item.validator=='project_plan':
            from .project_plan import validate_plan,InvalidProjectPlan
            try:
                validate_plan(response.get('content') or '',self.workspace,'benchmark_project',item.prompt)
                okay=True
            except (InvalidProjectPlan,ValueError,PermissionError,OSError):okay=False
            return ValidationResult(okay,True,True,F.SUCCESS if okay else F.VALIDATION_FAILURE,{},'project_plan')
        if item.validator=='unrated':return ValidationResult()
        text=response.get('content') or ''
        if item.validator=='proposal':
            proposals=getattr(response,'proposed_files',[])
            for proposal in proposals:
                if self.workspace:self.workspace.path(proposal['path'])
                if proposal['path']!=item.expected['path']:raise PermissionError('Proposal außerhalb des erlaubten Zielpfads')
            okay=proposals==[item.expected]
            return ValidationResult(okay,True,True,F.SUCCESS if okay else F.VALIDATION_FAILURE,{},'exact_value')
        if item.validator=='python':
            from .benchmark import check_python
            try:okay=check_python(text,item.expected)
            except SyntaxError:return ValidationResult(False,False,True,F.FORMAT_FAILURE,{},'format')
            except (ValueError,ZeroDivisionError,OverflowError):okay=False
        else:okay=text.strip()==str(item.expected)
        return ValidationResult(okay,True,True,F.SUCCESS if okay else F.VALIDATION_FAILURE,{},'exact_value')


class TrainingDemoBackend(MockBackend):
    """Only the explicitly labelled demo provider uses fixture reference answers."""
    async def chat_with_mode(self,messages,tools=None,model='',mode='native'):
        await asyncio.sleep(self.delay)
        value=json.loads(messages[-1]['content'])
        from .onboarding import catalog_v2
        task=next(t for t in [*catalog(),*catalog_v2()] if t.prompt==value['task'])
        if task.validator=='tool':return tool_reply(task.expected['name'],task.expected['arguments'])
        if task.validator=='proposal':text=json.dumps({'payload':'Vorschlag','status':'ok','proposed_files':[task.expected]})
        elif task.validator=='python':text=f'def solve(a,b): return a {task.expected} b'
        elif task.validator=='json':text=json.dumps(task.expected)
        else:text=str(task.expected)
        return {'role':'assistant','content':text}


class TrainingRunner:
    def __init__(self,registry,settings,store,models,categories=None,scope='quick',pool=None,session_id=None):
        self.registry,self.settings,self.store=registry,settings,store
        self.models=[registry.profiles[i] for i in models if i in registry.profiles and registry.profiles[i].enabled]
        if not self.models or categories==[]:raise ValueError('Mindestens ein Modell und eine Domain wählen.')
        self.scope={'short':'quick','normal':'standard'}.get(scope,scope)
        if self.scope not in {'quick','standard','lite','medium','hard','project_plan','file_transport'}:raise ValueError('Ungültiger Benchmark-Level.')
        if self.scope=='file_transport':
            from .onboarding import file_transport_qualification_tasks
            self.tasks=list(file_transport_qualification_tasks())
        elif self.scope=='project_plan':
            from .onboarding import project_plan_qualification_tasks
            self.tasks=list(project_plan_qualification_tasks())
        elif self.scope in {'lite','medium','hard'}:
            from .onboarding import catalog_v2
            selected=set(categories or DOMAINS)
            self.tasks=[t for t in catalog_v2() if t.category in selected]
        else:self.tasks=training_suite(categories)
        self.task_durations=[]
        self.pool=pool or BackendPool(settings.model.timeout)
        self.session_id=session_id;self.run_id=uuid.uuid4().hex
        self.stopped=threading.Event();self.observe=lambda *args:None
        self.results=[];self.status='PENDING';self.active_agent=None;self.call_attempts=0
        self.output_directory=store.database.parent/'benchmarks'/self.run_id
        self.plan={}

    def emit(self,kind,details):self.observe(kind,details,0,{})
    def check_stop(self):
        if self.stopped.is_set():raise asyncio.CancelledError()

    async def run(self,unused=''):
        self.plan={p.id:plan_for(p,self.tasks,self.scope) for p in self.models}
        if any(len(ts)>(128 if self.scope in {"lite","medium","hard"} else 16) for ts in self.plan.values()):raise ValueError('Tasklimit überschritten')
        self.planned=sum(map(len,self.plan.values()));self.status='RUNNING';self.started_at=time.monotonic()
        self.output_directory.mkdir(parents=True,exist_ok=False)
        self.store.benchmark_run(self.run_id,self.scope,self.status,self.planned,0)
        (self.output_directory/'plan.json').write_text(json.dumps({'suite_version':SUITE_VERSION,'scope':self.scope,
            'tasks':{key:[asdict(t) for t in ts] for key,ts in self.plan.items()}},ensure_ascii=False,indent=2),encoding='utf-8')
        try:
            for mi,profile in enumerate(self.models,1):
                self.check_stop()
                backend=self.pool.get(profile)
                if profile.provider=='mock' and type(backend) is MockBackend:backend=TrainingDemoBackend(delay=backend.delay)
                try:meta=await asyncio.wait_for(backend.model_identity(profile.model_name),5)
                except Exception:meta={}
                self.check_stop()
                fp=replace(ModelFingerprint.for_profile(profile,meta,self.settings.agent.context_budget),
                    max_output_tokens=self.settings.resources.output.default_limit)
                self.store.register(profile,fp);self.store.start_measurement(fp.id)
                for ti,task in enumerate(self.plan[profile.id],1):
                    self.check_stop()
                    from .onboarding import eta
                    current_eta=eta(self.task_durations,self.planned)
                    eta_label='Restzeit wird berechnet...' if current_eta is None else f'Geschätzt verbleibend: ~{current_eta/60:.1f} min'
                    self.emit('BENCHMARK_PROGRESS',{'status':f'Grundausbildung · {self.scope.upper()} · {len(self.results)}/{self.planned} erledigt · Vergangen: {(time.monotonic()-self.started_at)/60:.1f} min · {eta_label} · Modell {mi}/{len(self.models)} · Aufgabe {ti}/{len(self.plan[profile.id])} · {task.category} · {getattr(task,"difficulty","EASY")}',
                        'model_id':profile.id,'task_id':task.id,'category':str(task.category),'position':len(self.results)+1,'total':self.planned})
                    task_started=time.monotonic()
                    result=await self.measure(profile,backend,fp,task,meta)
                    self.task_durations.append(time.monotonic()-task_started)
                    from .onboarding import eta
                    remaining=eta(self.task_durations,self.planned)
                    elapsed=time.monotonic()-self.started_at
                    eta_text='Restzeit wird berechnet...' if remaining is None else f'Geschätzt verbleibend: ~{remaining/60:.1f} min'
                    self.emit('BENCHMARK_PROGRESS',{'status':f'Grundausbildung · {self.scope.upper()} · {len(self.results)+1}/{self.planned} Aufgaben · Vergangen: {elapsed/60:.1f} min · {eta_text}',
                        'benchmark_level':self.scope,'completed':len(self.results)+1,'total':self.planned,'elapsed_seconds':elapsed,'estimated_remaining_seconds':remaining})
                    self.results.append(result);self.export()
                    self.check_stop()
                    self.store.benchmark_run(self.run_id,self.scope,'RUNNING',self.planned,len(self.results),{'model_calls':self.call_attempts})
                    self.emit('EXPERIENCE_RECORDED',{**result,'status':f'{task.category}: {result["verified_status"]} · {result["failure_type"]}'})
                    self.emit('EXPERIENCE_AGGREGATED',{'category':str(task.category)})
            self.status='COMPLETED'
        except asyncio.CancelledError:
            self.status='ABORTED';self.export()
            self.emit('STOP',{'status':f'Grundausbildung ABORTED · {len(self.results)} Ergebnisse erhalten; keine neue Aufgabe.'})
            raise
        except Exception:
            self.status='FAILED';self.export();raise
        finally:
            self.active_agent=None
            self.store.benchmark_run(self.run_id,self.scope,self.status,self.planned,len(self.results),{'model_calls':self.call_attempts,'csv':str(self.output_directory/'benchmark_results.csv')})
        return f'Grundausbildung {self.status}: {len(self.results)} Aufgaben, {self.call_attempts} Modellaufrufe. Daten bleiben lokal; kleine Samples sind keine Qualitätsgarantie.'

    async def measure(self,profile,backend,fp,task,meta):
        # Legacy public test/API tasks remain readable; the GUI/catalog only emits v3 tasks.
        mode=getattr(task,'response_contract','STRUCTURED_JSON' if task.validator=='json' else 'TOOL_ACTION' if task.validator=='tool' else 'PLAIN_TEXT')
        difficulty=getattr(task,'difficulty','EASY');fixtures=getattr(task,'fixtures',())
        self.check_stop();started=time.monotonic();observed=[];error=None;requested_tools=0
        tool_observation=({'selected':False,'name_correct':False,'arguments_correct':False,'permission_allowed':None,
            'tool_successful':False,'unnecessary_repeats':0,'extra_calls':0,'loop':False} if task.validator=='tool' else {})
        with tempfile.TemporaryDirectory(prefix='alienape-training-') as temporary:
            workspace=Workspace(temporary)
            for name,contents in fixtures:
                path=workspace.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(contents,encoding='utf-8')
            agent=Agent(backend,workspace,self.settings,stopped=self.stopped,validator=TrainingValidator(task,workspace))
            self.active_agent=agent;agent.prepare_task(task.prompt)
            if task.validator in {'project_plan','file_content_python','native_write_python'}:
                # A benchmark prompt is not a user request for file mutation.
                from .contracts import WriteScope
                agent.intent_resolution=None
                agent.write_scope=WriteScope()
                agent.workspace_operation=None
                # These fixed qualification cases have no input files. A target
                # named in the prompt is an output, never a required read.
                agent.required_input_paths=[]
                if task.validator in {'file_content_python','native_write_python'}:
                    agent.write_scope=WriteScope(False,[task.expected['target']],True,True)
            agent.model_call_limit=min(1 if self.scope in {'project_plan','file_transport'} else 2,self.settings.agent.max_steps)
            agent.resources.model=profile.model_name
            agent.response_contract=ResponseContract(M(mode),mode=='STRUCTURED_JSON',
                required_keys=list(task.expected) if task.validator=='json' and isinstance(task.expected,dict) else [],
                expects_file_proposal=mode=='FILE_PROPOSAL',expects_plain_text=mode in {'PLAIN_TEXT','ANALYSIS_RESULT'})
            agent.known_context_limit=profile.context_length or meta.get('context_length') or backend.get_capabilities().get('context_length')
            def observe(kind,details,*rest):
                if kind=='MODEL_CALL':
                    self.call_attempts+=1
                    if self.call_attempts>2*self.planned:raise RuntimeError('Globales Modellcall-Limit')
                if kind=='MODEL_COMPLETION':observed.append(details)
                if kind in {'RESOURCE_OUTPUT_LIMIT','RESOURCE_RETRY_STARTED','RESOURCE_RETRY_SUCCESS','PROTOCOL_NORMALIZED'}:
                    self.emit(kind,{**details,'model_id':profile.id,'task_id':task.id})
            agent.observe=observe
            files=[]
            try:
                if task.validator!='tool':
                    for name,contents in fixtures:
                        data=await agent.execute_tool('read_file',{'path':name},origin='controller')
                        files.append({'path':name,'content':data['content']})
                schemas=agent.tools.schemas({'write_file'},write_scope=agent.write_scope) if task.validator=='native_write_python' else agent.tools.schemas({'read_file','search_files'}) if task.validator=='tool' else None
                agent.active_tool_names={'write_file'} if task.validator=='native_write_python' else {'read_file','search_files'} if schemas else set()
                packet=TaskPacket(task.prompt,'BENCHMARK',[str(task.category)],relevant_files_or_contents=files,
                    resource_budget={'context_bytes':self.settings.agent.context_budget,'max_model_steps':2,
                                     'max_output_tokens':self.settings.resources.output.default_limit},task_domain=str(task.category),
                    response_contract=asdict(agent.response_contract),write_scope=asdict(agent.write_scope)).bounded(self.settings.agent.context_budget)
                messages=packet.messages()
                response=await agent.call_model(task.prompt,messages,schemas,
                    lambda:backend.chat_with_mode(messages,schemas,profile.model_name,profile.tool_mode),backend=backend,structured_protocol=mode=='FILE_PROPOSAL')
                if task.validator=='native_write_python':
                    from .benchmark import check_python
                    calls=response.get('tool_calls') or []
                    requested_tools=len(calls)
                    if len(calls)!=1:raise CompletionRejected(F.FORMAT_FAILURE,'Expected one native write_file call')
                    function=calls[0].get('function',{})
                    try:
                        args=json.loads(function['arguments']) if isinstance(function.get('arguments'),str) else function['arguments']
                        if function.get('name')!='write_file':raise ValueError('wrong tool')
                        agent.tools.validate('write_file',args)
                        if not agent.write_scope.permits(args['path'],workspace):raise PermissionError('WRITE_SCOPE')
                    except (KeyError,ValueError,TypeError,PermissionError,ToolArgumentInvalid) as exc:
                        agent.emit('TOOL_DENIED',{'name':'write_file','subreason':'WRITE_SCOPE_OR_INVALID_ARGUMENTS','dispatch':False})
                        tool_observation={'selected':True,'write_scope_checked':False,'write_performed':False,'denied':True}
                        raise CompletionRejected(F.FORMAT_FAILURE,'Invalid or unauthorized tool call') from exc
                    tool_observation={'selected':True,'name_correct':True,'arguments_correct':True,'permission_allowed':None,'tool_successful':False,'write_scope_checked':True,'write_performed':False}
                    try:
                        import ast
                        ast.parse(args['content'])
                        okay=check_python(args['content'],task.expected['symbol'])
                    except (SyntaxError,ValueError,ZeroDivisionError,OverflowError):okay=False
                    failure=F.SUCCESS if okay else F.VALIDATION_FAILURE
                    status=S.VERIFIED_SUCCESS if okay else S.VERIFIED_FAILURE
                    agent.last_failure_type=failure
                elif task.validator=='tool':
                    calls=response.get('tool_calls') or []
                    requested_tools=len(calls)
                    tool_observation={'selected':bool(calls),'name_correct':False,'arguments_correct':False,
                        'permission_allowed':None,'tool_successful':False,'unnecessary_repeats':len(calls)-len({json.dumps(c.get('function'),sort_keys=True) for c in calls}),'extra_calls':max(0,len(calls)-1),'loop':False}
                    if len(calls)!=1:raise CompletionRejected(F.TOOL_FAILURE,'Genau ein Toolaufruf erwartet')
                    function=calls[0]['function'];args=function['arguments']
                    if isinstance(args,str):args=json.loads(args)
                    expected=task.expected
                    if 'name' not in expected:expected={'name':'read_file','arguments':expected}
                    tool_observation['name_correct']=function['name']==expected['name']
                    tool_observation['arguments_correct']=args==expected['arguments']
                    if not all(tool_observation[k] for k in ['name_correct','arguments_correct']):raise CompletionRejected(F.TOOL_FAILURE,'Toolname/Argumente entsprechen nicht dem Task')
                    await agent.execute_tool(function['name'],args)
                    tool_observation.update(permission_allowed=True,tool_successful=True)
                    agent.last_failure_type=F.SUCCESS
                elif response.get('tool_calls'):raise CompletionRejected(F.TOOL_FAILURE,'Keine Tools für diesen Antwortvertrag angeboten')
                if task.validator!='native_write_python':
                    failure=agent.last_failure_type
                    status=S.VERIFIED_SUCCESS if task.validator!='unrated' and failure==F.SUCCESS else S.UNKNOWN
            except asyncio.CancelledError:raise
            except Exception as exc:
                error=type(exc).__name__
                import httpx
                if isinstance(exc,CompletionRejected):failure=exc.failure_type
                elif isinstance(exc,FileNotFoundError):failure=F.INFORMATION_MISSING;agent.information_complete=False
                elif isinstance(exc,(httpx.TimeoutException,TimeoutError)):failure=F.TIMEOUT
                elif isinstance(exc,(ToolArgumentInvalid,PermissionError)):failure=F.TOOL_FAILURE if isinstance(exc,ToolArgumentInvalid) else F.PERMISSION_FAILURE
                elif isinstance(exc,httpx.HTTPError):failure=F.UNKNOWN
                else:failure=F.FORMAT_FAILURE
                if task.validator=='native_write_python' and failure in {F.TOOL_FAILURE,F.FORMAT_FAILURE} and agent.last_completion.finish_reason.casefold() in {'length','max_tokens','max_output_tokens','limit'}:
                    failure=F.RESOURCE_OUTPUT_LIMIT
                if failure==F.UNKNOWN and agent.last_completion.finish_reason.casefold() in {'length','max_tokens','max_output_tokens','limit'}:
                    # Exact text can be wrong without proving how much more output is needed.
                    # Do not turn a clipped plain response into negative subject competence.
                    failure=F.RESOURCE_OUTPUT_LIMIT
                elif failure==F.UNKNOWN and agent.last_validation.valid is False and agent.last_validation.verified:
                    failure=agent.last_validation.failure_type
                status=S.VERIFIED_FAILURE if failure in {F.FORMAT_FAILURE,F.VALIDATION_FAILURE,F.TOOL_FAILURE} else S.RESOURCE_FAILURE if task.validator=='native_write_python' and failure in {F.RESOURCE_OUTPUT_LIMIT,F.RESOURCE_CONTEXT_LIMIT} else S.UNKNOWN
            self.check_stop()
            counted=[c for c in observed if c.get('input_tokens') is not None and c.get('output_tokens') is not None]
            exact=len(counted)==agent.resources.counters.model_calls
            counts=agent.resources.counters
            token_in=sum(c['input_tokens'] for c in counted) if exact else counts.input_tokens_estimate
            token_out=sum(c['output_tokens'] for c in counted) if exact else counts.output_tokens_estimate
            final=observed[-1] if observed else {}
            context_bytes=getattr(backend,'last_context_bytes',None) or counts.context_size
            result={'benchmark_id':self.run_id,'suite_version':SUITE_VERSION,'model_id':profile.id,'model_fingerprint':fp.id,
                'benchmark_level':self.scope if self.scope in {'lite','medium','hard'} else None,'benchmark_catalog_version':getattr(task,'benchmark_catalog_version',None),'benchmark_run_id':self.run_id,
                'capability_v2':getattr(task,'capability',None),'language_v2':getattr(task,'language',None),'protocol_v2':getattr(task,'protocol_required',None),
                'task_id':task.id,'task_domain':str(task.category),'response_contract':mode,'difficulty':difficulty,
                'verified_status':str(status),'failure_type':str(failure),'input_tokens':token_in,'output_tokens':token_out,
                'tokens_estimated':not exact,'latency_ms':round((time.monotonic()-started)*1000,3),
                'model_calls':counts.model_calls,'tool_calls':requested_tools if task.validator in {'tool','native_write_python'} else counts.tool_calls,
                'invalid_tool_calls':max(1,requested_tools) if tool_observation and not tool_observation.get('tool_successful') else 0,
                'resource_retries':counts.resource_retries,'escalations':0,'context_bytes':context_bytes,
                'information_complete':agent.information_complete,'protocol_normalized':agent.protocol_normalized,
                'requested_output_limit':final.get('requested_output_limit'),'actual_output_tokens':final.get('output_tokens'),
                'initial_requested_limit':observed[0].get('requested_output_limit') if observed else self.settings.resources.output.default_limit,
                'successful_requested_limit':final.get('requested_output_limit') if status==S.VERIFIED_SUCCESS else None,
                'actual_success_output_tokens':final.get('output_tokens') if status==S.VERIFIED_SUCCESS else None,
                'finish_reason':final.get('finish_reason','UNKNOWN'),'tool_observation':tool_observation,'error_type':error,'source':'benchmark'}
            # Only evidence independently observed for a complete structured JSON
            # response is recorded; task validation remains a separate signal.
            protocol_status=None
            last=getattr(agent,'last_response',None)
            neutral={F.RESOURCE_OUTPUT_LIMIT,F.RESOURCE_CONTEXT_LIMIT,F.TIMEOUT,F.INFORMATION_MISSING,F.PERMISSION_FAILURE}
            if mode=='STRUCTURED_JSON' and agent.information_complete and failure not in neutral and isinstance(last,dict) and not last.get('tool_calls') and str(final.get('finish_reason','')).casefold() in {'stop','end_turn'}:
                valid_json=JSONValidator().validate('',last).valid is True
                protocol_status='VERIFIED_SUCCESS' if valid_json and not agent.protocol_normalized else 'VERIFIED_FAILURE'
            if task.validator=='project_plan' and protocol_status=='VERIFIED_FAILURE':
                # A salvaged/fenced response is not a valid strict project task result.
                status=S.UNKNOWN
                result['verified_status']=str(status)
            if task.validator=='file_content_python' and failure not in neutral and agent.information_complete:
                protocol_status=agent.file_content_protocol_status
                if protocol_status=='VERIFIED_FAILURE':status=S.UNKNOWN;result['verified_status']=str(status)
            if task.validator=='native_write_python' and failure not in neutral and agent.information_complete:
                finish=str(final.get('finish_reason','')).casefold()
                if finish in {'stop','end_turn','tool_calls'}:
                    protocol_status='VERIFIED_SUCCESS' if tool_observation.get('write_scope_checked') else 'VERIFIED_FAILURE'
                    if protocol_status=='VERIFIED_FAILURE':status=S.UNKNOWN;result['verified_status']=str(status)
            result['protocol_status']=protocol_status
            identifier=self.store.record(fp.id,task.category,status,subcategory=task.id,source='benchmark',benchmark_id=self.run_id,suite_version=SUITE_VERSION,
                session_id=self.session_id,task_domain=str(task.category),response_contract=mode,difficulty=difficulty,
                failure_type=str(failure),verified_status=str(status),information_complete=agent.information_complete,
                protocol_normalized=agent.protocol_normalized,attribution_version='v0.5',latency_ms=result['latency_ms'],
                input_tokens=token_in,output_tokens=token_out,tokens_estimated=not exact,context_bytes=context_bytes,
                model_calls=counts.model_calls,tool_calls=result['tool_calls'],invalid_tool_calls=result['invalid_tool_calls'],
                finish_reason=result['finish_reason'],requested_output_limit=result['requested_output_limit'],actual_output_tokens=result['actual_output_tokens'],
                initial_requested_limit=result['initial_requested_limit'],successful_requested_limit=result['successful_requested_limit'],
                actual_success_output_tokens=result['actual_success_output_tokens'],resource_retries=counts.resource_retries,escalations=0,
                resource_retry_used=bool(counts.resource_retries),resource_retry_success=bool(counts.resource_retries and status==S.VERIFIED_SUCCESS),
                tool_observation=tool_observation,benchmark_task_id=task.id,protocol_status=protocol_status,
                **{key:result[key] for key in ["benchmark_level","benchmark_catalog_version","benchmark_run_id","capability_v2","language_v2","protocol_v2"]})
            result.update(id=identifier,category=str(task.category),fingerprint=fp.id,signal=str(status))
            return result

    def export(self):
        if not self.output_directory.exists():return
        fields=list(self.results[0]) if self.results else ['benchmark_id','suite_version','task_id','verified_status']
        path=self.output_directory/'benchmark_results.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,delimiter=';');writer.writeheader()
            for row in redactor.clean(self.results):writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
