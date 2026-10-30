"""Bounded objective adapters. Executables/arguments are never supplied by the model."""
from dataclasses import dataclass,field,asdict
from enum import StrEnum
from pathlib import Path
import ast,asyncio,hashlib,json,os,re,shutil,signal,sys,time,uuid
from .intents import is_test_path
from .security import redactor, verifier_environment, SensitiveFilePolicy

class VerificationStatus(StrEnum):
    NOT_RUN='NOT_RUN'
    PASS='PASS'
    FAIL='FAIL'
    UNAVAILABLE='UNAVAILABLE'
    ERROR='ERROR'
    TIMEOUT='TIMEOUT'
    ABORTED='ABORTED'

@dataclass(frozen=True)
class VerificationPlan:
    verifier_type:str
    workspace:str
    files:tuple
    test_files:tuple=()
    expected_scope:str='selected files and tests; not a complete project proof'
    baseline:bool=False
    timeout_seconds:float=30
    output_limit:int=8192
    requires_permission:bool=False
    verification_id:str=field(default_factory=lambda:uuid.uuid4().hex)
    verification_version:str='v0.6.3'

@dataclass
class VerificationResult:
    verification_status:str='NOT_RUN'
    verifier_type:str='NONE'
    baseline_status:str='NOT_RUN'
    post_status:str='NOT_RUN'
    exit_code:int|None=None
    duration_ms:float=0
    timeout:bool=False
    files_verified:list=field(default_factory=list)
    verification_id:str=''
    verification_version:str='v0.6.3'
    task_signal:str='UNKNOWN'
    message:str='Keine Verifikation ausgeführt.'
    baseline:dict=field(default_factory=dict)
    post:dict=field(default_factory=dict)

    def compact(self):
        return {k:v for k,v in asdict(self).items() if k not in {'baseline','post'}}

def trusted_python(config):
    if config.python_executable:return str(Path(config.python_executable).resolve())
    if not getattr(sys,'frozen',False):return sys.executable
    development=Path(sys.executable).parents[2]/'.venv/Scripts/python.exe'
    return str(development) if development.is_file() else shutil.which('python')

def trusted_godot(config):
    return str(Path(config.godot_executable).resolve()) if config.godot_executable else shutil.which('godot') or shutil.which('godot4')

class VerificationRunner:
    def __init__(self,workspace,config,check_stop,permission,emit):
        self.workspace,self.config,self.check_stop,self.permission,self.emit=workspace,config,check_stop,permission,emit
        self.python,self.godot=trusted_python(config),trusted_godot(config)
        self.plan=None;self.baseline={};self.result=VerificationResult();self.finished=False
        self.existed={}

    def create_plan(self,paths):
        if self.config.mode=='OFF':return None
        files=[]
        for name in paths:
            SensitiveFilePolicy.writable(name)
            safe=self.workspace.path(name)
            if safe.suffix.casefold() in {'.py','.gd','.tscn','.tres'}:files.append(name)
        if not files:return None
        self.existed={name:self.workspace.path(name).exists() for name in files}
        tests=[];scanned=0
        # Small direct test directories only; no full repository collection.
        for directory in [self.workspace.root,self.workspace.root/'tests',self.workspace.root/'test']:
            if not directory.exists():continue
            safe=self.workspace.path(directory.relative_to(self.workspace.root).as_posix())
            with os.scandir(safe) as entries:
                for entry in entries:
                    scanned+=1
                    if scanned>200:break
                    relative=Path(entry.path).relative_to(self.workspace.root).as_posix()
                    path=self.workspace.path(relative)
                    if path.is_file() and path.suffix=='.py' and is_test_path(relative):tests.append(relative)
            if scanned>200:break
        tests=list(dict.fromkeys(tests+[p for p in files if p.endswith('.py') and is_test_path(p)]))
        if len(tests)>8:tests=[] # Avoid silently certifying only a subset of a large suite.
        python=all(p.endswith('.py') for p in files)
        kind='PYTHON_TESTS' if python and tests else 'PYTHON_AST' if python else 'GODOT_CHECK' if all(p.endswith(('.gd','.tscn','.tres')) for p in files) else 'UNSUPPORTED'
        existing=any(self.workspace.path(p).is_file() for p in files)
        self.plan=VerificationPlan(kind,str(self.workspace.root),tuple(files),tuple(tests),baseline=existing,
            timeout_seconds=self.config.timeout_seconds,output_limit=self.config.output_limit,requires_permission=kind in {'PYTHON_TESTS','GODOT_CHECK'})
        self.result=VerificationResult(verifier_type=kind,verification_id=self.plan.verification_id,files_verified=files)
        self.emit('VERIFICATION_PLAN_CREATED',{**asdict(self.plan),'status':'Objektive Prüfung vor dem Write festgelegt'})
        return self.plan

    def syntax(self,files,*,existing_only=False):
        problems=[];verified=[]
        for name in files:
            self.check_stop();path=self.workspace.path(name)
            if existing_only and not path.exists():continue
            try:
                if not path.exists() and not existing_only and not self.existed.get(name,True):
                    problems.append({'file':name,'line':None,'type':'MissingOutput','message':'Geplante neue Datei wurde nicht erstellt.'});continue
                with path.open('rb') as stream:data=stream.read(262145)
                if len(data)>262144:return {'status':'ERROR','message':'Syntaxdatei überschreitet 256 KB.','files_verified':verified}
                ast.parse(data.decode('utf-8-sig'),filename=name);verified.append(name)
            except SyntaxError as exc:problems.append({'file':name,'line':exc.lineno,'type':type(exc).__name__,'message':str(exc.msg)[:300]})
            except (OSError,UnicodeError) as exc:return {'status':'ERROR','message':str(exc)[:300],'files_verified':verified}
        return {'status':'FAIL' if problems else 'PASS','files_verified':verified,'syntax_errors':problems,'message':str(problems)[:600] if problems else 'Python AST PASS'}

    async def authorize(self,kind):
        self.check_stop()
        if not self.permission:return False
        return await self.permission(kind,{'path':'.','verifier_type':self.plan.verifier_type,'files':list(self.plan.files),'tests':list(self.plan.test_files),'timeout_seconds':self.plan.timeout_seconds},self.workspace.root)

    async def terminate(self,process):
        if process.returncode is not None:return
        if os.name=='nt':
            killer=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/taskkill.exe'
            if killer.is_file():
                try:
                    p=await asyncio.create_subprocess_exec(str(killer),'/PID',str(process.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(p.wait(),3)
                except (OSError,TimeoutError):pass
        else:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        if process.returncode is None:
            try:process.kill()
            except ProcessLookupError:pass
        await process.wait()

    async def process(self,args):
        """Private fixed-adapter runner: streaming hashes, bounded retained output, no shell."""
        self.check_stop()
        self.workspace.path('.')
        for selected in (*self.plan.files,*self.plan.test_files):self.workspace.path(selected)
        if self.plan.workspace!=str(self.workspace.root):raise PermissionError('Verification cwd differs from workspace')
        runtime=self.workspace.path('.alienape-verification/runtime');runtime.mkdir(parents=True,exist_ok=True)
        if args[0]==self.python:
            # -I ignores PYTHON* environment variables. Explicit flags prevent stale
            # workspace pyc imports and prevent verification from writing bytecode.
            args=[args[0],'-B','-X','pycache_prefix='+str(runtime/uuid.uuid4().hex),*args[1:]]
        env=dict(verifier_environment(),PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTEST_ADDOPTS='',PYTHONIOENCODING='utf-8',APPDATA=str(runtime),LOCALAPPDATA=str(runtime),TEMP=str(runtime),TMP=str(runtime))
        env.pop('PYTHONPATH',None);env.pop('PYTHONSTARTUP',None)
        env.pop('PYTEST_PLUGINS',None)
        started=time.monotonic();process=None;readers=[]
        streams={name:{'data':bytearray(),'hash':hashlib.sha256(),'bytes':0} for name in ['stdout','stderr']}
        async def consume(stream,name):
            record=streams[name]
            while True:
                chunk=await stream.read(4096)
                if not chunk:break
                record['hash'].update(chunk);record['bytes']+=len(chunk)
                record['data'].extend(chunk[:max(0,self.plan.output_limit-len(record['data']))])
        status='ERROR';message=''
        try:
            kwargs={'creationflags':0x00000200} if os.name=='nt' else {'start_new_session':True}
            process=await asyncio.create_subprocess_exec(*args,cwd=self.workspace.root,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,**kwargs)
            readers=[asyncio.create_task(consume(process.stdout,'stdout')),asyncio.create_task(consume(process.stderr,'stderr'))]
            deadline=min(started+self.plan.timeout_seconds,getattr(self,'execution_deadline',float('inf')))
            while process.returncode is None:
                self.check_stop()
                if time.monotonic()>deadline:status='TIMEOUT';await self.terminate(process);break
                await asyncio.sleep(.03)
            else:status='COMPLETED'
        except asyncio.CancelledError:
            status='ABORTED'
            if process:await self.terminate(process)
        except (OSError,PermissionError) as exc:message=str(exc)[:300];status='UNAVAILABLE' if isinstance(exc,FileNotFoundError) else 'ERROR'
        finally:
            if process and process.returncode is None:await self.terminate(process)
            if readers:await asyncio.gather(*readers,return_exceptions=True)
        return redactor.clean({'status':status,'exit_code':process.returncode if process else None,'duration_ms':round((time.monotonic()-started)*1000,3),'timeout':status=='TIMEOUT','message':message,
            **{name:bytes(s['data']).decode('utf-8',errors='replace') for name,s in streams.items()},
            **{name+'_sha256':s['hash'].hexdigest() for name,s in streams.items()},**{name+'_bytes':s['bytes'] for name,s in streams.items()}})

    async def executable_check(self):
        p=self.plan
        kind='verify_python_tests' if p.verifier_type=='PYTHON_TESTS' else 'verify_godot'
        if not await self.authorize(kind):return {'status':'UNAVAILABLE','message':'Ausführungspermission verweigert oder nicht verfügbar.'}
        self.check_stop()
        self.execution_deadline=time.monotonic()+p.timeout_seconds
        if p.verifier_type=='PYTHON_TESTS':
            if not self.python or not Path(self.python).is_file():return {'status':'UNAVAILABLE','message':'Python-Verifier-Executable fehlt.'}
            # Isolated metadata probe, after execution permission; no workspace imports.
            probe=await self.process([self.python,'-I','-c',"import importlib.util; print('PYTEST_PRESENT' if importlib.util.find_spec('pytest') else 'PYTEST_ABSENT')"])
            if probe['status']!='COMPLETED':return probe
            if probe['exit_code']!=0:return {**probe,'status':'ERROR','message':'Python-Verifier-Probe fehlgeschlagen.'}
            if probe['stdout'].strip() not in {'PYTEST_PRESENT','PYTEST_ABSENT'}:return {**probe,'status':'ERROR','message':'Python-Verifier-Probe lieferte keine sichere Framework-Evidenz.'}
            if probe['stdout'].strip()=='PYTEST_PRESENT':
                # pytest parses this INI value with POSIX shlex even on Windows.
                pythonpath=json.dumps(Path(p.workspace).as_posix(),ensure_ascii=False)
                args=[self.python,'-I','-m','pytest','-q','-p','no:cacheprovider','--rootdir',p.workspace,'--confcutdir',p.workspace,'-o','addopts=','-o','pythonpath='+pythonpath,*p.test_files]
                outcome=await self.process(args);adapter='PYTHON_PYTEST'
                if outcome['status']=='COMPLETED':
                    outcome['status']='PASS' if outcome['exit_code']==0 else 'FAIL' if outcome['exit_code']==1 else 'UNAVAILABLE' if outcome['exit_code']==5 else 'ERROR'
            else:
                # Only obvious unittest fixtures; never silently run a different test framework.
                if not all(re.search(r'\b(?:unittest|TestCase)\b',self.workspace.path(f).read_text(encoding='utf-8')) for f in p.test_files):return {'status':'UNAVAILABLE','message':'pytest nicht verfügbar; keine eindeutigen unittest-Tests.'}
                adapter='PYTHON_UNITTEST'
                for test in p.test_files:
                    path=Path(test)
                    args=[self.python,'-m','unittest','discover','-s',str(path.parent),'-p',path.name,'-q']
                    outcome=await self.process(args)
                    if outcome['status']=='COMPLETED':outcome['status']='ERROR' if 'Failed to import test module' in outcome['stderr'] else 'UNAVAILABLE' if outcome['exit_code']==5 else 'PASS' if outcome['exit_code']==0 else 'FAIL'
                    if outcome['status']=='PASS' and not re.search(r'Ran\s+[1-9]\d*\s+tests?',outcome['stderr']):outcome.update(status='UNAVAILABLE',message='unittest hat keine ausgeführten Tests nachgewiesen.')
                    if outcome['status']!='PASS':break
            return {**outcome,'adapter':adapter,'message':outcome.get('message') or adapter+' '+outcome['status']}
        if not self.godot or not Path(self.godot).is_file():return {'status':'UNAVAILABLE','message':'Godot-CLI nicht verfügbar.'}
        outcomes=[]
        for f in p.files:
            if not self.workspace.path(f).exists():return {'status':'ERROR','message':'Godot-Zieldatei fehlt.'}
            args=[self.godot,'--headless','--path',p.workspace,'--log-file',os.devnull]
            if f.endswith('.gd'):args+=['--check-only','--script','res://'+f]
            else:
                helper=self.workspace.path('.alienape-verification/resource_check.gd');helper.parent.mkdir(parents=True,exist_ok=True)
                helper.write_text('extends SceneTree\nfunc _initialize() -> void:\n\tvar paths = OS.get_cmdline_user_args()\n\tif paths.size() != 1:\n\t\tquit(2); return\n\tvar resource = ResourceLoader.load(paths[0])\n\tif resource == null:\n\t\tpush_error("ALIENAPE_RESOURCE_LOAD_FAILED"); quit(1); return\n\tprint("ALIENAPE_RESOURCE_CHECK_PASS"); quit(0)\n',encoding='utf-8')
                args+=['--script','res://.alienape-verification/resource_check.gd','--','res://'+f]
            outcome=await self.process(args)
            if outcome['status']=='COMPLETED':
                text=outcome['stdout']+outcome['stderr']
                outcome['status']='FAIL' if re.search(r'Parse Error|Parse error|SCRIPT ERROR:',text) else 'PASS' if outcome['exit_code']==0 else 'ERROR'
            outcomes.append(outcome)
            if outcome['status']!='PASS':break
        return {**outcomes[-1],'adapter':'GODOT_CHECK','checks':outcomes,'message':'Godot '+outcomes[-1]['status']}

    async def prepare(self,paths):
        if self.plan or self.finished:return
        if not self.create_plan(paths):return
        try:
            if self.plan.baseline:
                started=time.monotonic()
                syntax=self.syntax([p for p in self.plan.files if p.endswith('.py')],existing_only=True)
                self.baseline={'status':syntax['status'],'syntax':syntax}
                if syntax['status']=='PASS' and self.plan.requires_permission:
                    self.baseline=await self.executable_check();self.baseline['syntax']=syntax
                self.baseline['duration_ms']=round((time.monotonic()-started)*1000,3)
                self.emit('VERIFICATION_BASELINE',{**self.baseline,'verification_id':self.plan.verification_id,'status':'Baseline: '+self.baseline['status']})
                if self.baseline['status']=='ABORTED':raise asyncio.CancelledError()
        except asyncio.CancelledError:
            self.abort();raise
        except Exception as exc:
            self.baseline={'status':'ERROR','message':str(exc)[:300]}

    def abort(self):
        self.finished=True
        self.result.verification_status=self.result.post_status='ABORTED';self.result.message='Verifikation durch STOP abgebrochen.'
        self.emit('VERIFICATION_COMPLETED',{**self.result.compact(),'status':'Verification: ABORTED'})

    async def inspect(self,paths):
        """Read-only project check; no invented model/task success from check status."""
        if not self.create_plan(paths):return self.result
        return await self.post(paths,inspection=True)

    async def post(self,written,*,inspection=False):
        if self.finished:return self.result
        if not written or not self.plan:
            self.finished=True;return self.result
        try:
            started=time.monotonic()
            self.check_stop()
            syntax=self.syntax([p for p in self.plan.files if p.endswith('.py')])
            outcome=syntax if syntax['status']!='PASS' or self.plan.verifier_type=='PYTHON_AST' else await self.executable_check() if self.plan.requires_permission else {'status':'UNAVAILABLE','message':'Gemischte Verifier-Sprache nicht unterstützt.'}
            if outcome['status']=='ABORTED':self.abort();raise asyncio.CancelledError()
            outcome['duration_ms']=round((time.monotonic()-started)*1000,3)
            before=self.baseline.get('status','NOT_APPLICABLE' if not self.plan.baseline else 'NOT_RUN')
            status=outcome['status'];signal='UNKNOWN'
            if status=='PASS' and self.plan.requires_permission:signal='VERIFIED_SUCCESS'
            if status=='FAIL' and (before=='PASS' or syntax['status']=='FAIL' and self.baseline.get('syntax',{}).get('status','PASS')=='PASS' or not self.plan.baseline):signal='VERIFIED_FAILURE'
            if inspection:signal='UNKNOWN';before='NOT_APPLICABLE'
            self.result=VerificationResult(status,self.plan.verifier_type,before,status,outcome.get('exit_code'),outcome.get('duration_ms',0),status=='TIMEOUT',list(self.plan.files),self.plan.verification_id,task_signal=signal,message=outcome.get('message',status),baseline=self.baseline,post=outcome)
            self.finished=True
            evidence=self.workspace.path('.alienape-verification/'+self.plan.verification_id+'.json');evidence.parent.mkdir(parents=True,exist_ok=True)
            value=redactor.clean(asdict(self.result));encoded=json.dumps(value,ensure_ascii=False,indent=2)
            if len(encoded.encode())>65536:
                value={**self.result.compact(),'full_result_sha256':hashlib.sha256(encoded.encode()).hexdigest(),'evidence_truncated':True,
                    'baseline':{k:v for k,v in self.baseline.items() if k not in {'stdout','stderr','checks'}},
                    'post':{k:v for k,v in outcome.items() if k not in {'stdout','stderr','checks'}}}
                encoded=json.dumps(value,ensure_ascii=False,indent=2)
            evidence.write_text(encoded,encoding='utf-8')
            self.emit('VERIFICATION_COMPLETED',{**self.result.compact(),'evidence_path':evidence.relative_to(self.workspace.root).as_posix(),'status':'Verification: '+status+' · '+self.result.message})
            return self.result
        except asyncio.CancelledError:
            if not self.finished:self.abort()
            raise
        except Exception as exc:
            self.result.verification_status=self.result.post_status='ERROR';self.result.task_signal='UNKNOWN'
            self.result.message=str(exc)[:300];self.finished=True
            self.emit('VERIFICATION_COMPLETED',{**self.result.compact(),'status':'Verification: ERROR'})
            return self.result
