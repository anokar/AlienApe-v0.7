"""Small ephemeral operational state. Not chat memory and never persisted."""
from dataclasses import dataclass,field,asdict
from datetime import datetime,timezone
from enum import StrEnum
import hashlib,os,re,uuid

def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

class FindingStatus(StrEnum):
    HYPOTHESIS='HYPOTHESIS'
    SUSPECTED='SUSPECTED'
    VERIFIED='VERIFIED'
    REJECTED='REJECTED'
    UNVERIFIED='UNVERIFIED'

@dataclass
class Finding:
    finding_id:str
    summary:str
    files:list
    evidence_type:str
    evidence_reference:str
    status:str
    created_from_task_id:str
    verifier_type:str=''
    verification_result:str=''
    symptom_status:str='UNVERIFIED'
    cause_status:str='UNVERIFIED'
    repair_targets:list=field(default_factory=list)

@dataclass
class SessionTaskState:
    active_workspace:str=''
    last_worker_model:str=''
    last_task_id:str=''
    last_task_intent:str=''
    last_inspected_files:list=field(default_factory=list)
    last_verification_summary:list=field(default_factory=list)
    current_findings:list=field(default_factory=list)
    last_mutation_scope:dict=field(default_factory=dict)
    file_hashes:dict=field(default_factory=dict)
    generation:int=0
    timestamp:str=''
    previous_failure_context:dict=field(default_factory=dict)
    pending_task:object=None
    pending_choice:object=None
    last_generated_artifact:object=None
    continuity_turn:int=0

    def bind(self,workspace):
        key=os.path.normcase(str(workspace.root))
        if key!=self.active_workspace:
            self.__dict__.update(SessionTaskState(active_workspace=key,generation=self.generation+1).__dict__)

    def clear_findings(self):
        self.current_findings=[];self.last_inspected_files=[];self.last_verification_summary=[];self.file_hashes={}

    def commit(self,workspace,task_id,intent,files,verification,findings,scope):
        self.bind(workspace);self.last_task_id=task_id;self.last_task_intent=intent
        self.last_inspected_files=list(dict.fromkeys(files))[:8];self.last_verification_summary=verification[:2]
        self.current_findings=findings[:8];self.last_mutation_scope=scope;self.generation+=1
        self.timestamp=datetime.now(timezone.utc).isoformat();self.file_hashes={}
        for f in self.current_findings:
            for p in f.files:
                path=workspace.path(p)
                if path.is_file():self.file_hashes[p]=file_hash(path)

def followup_intent(task):
    from .routing import referenced_paths
    if referenced_paths(task):return False
    text=' '.join(task.casefold().strip().rstrip('.!?').split())
    prefix=r'(?:(?:kannst|kanst) du (?:bitte )?|can you (?:please )?|(?:please|pleas|bitte) )?'
    return bool(re.fullmatch(prefix+r'(?:fix(?:e)? (?:die bugs|die fehler|das|the bugs|the errors|them)|(?:repariere|behebe|korrigiere) (?:die (?:gefundenen )?fehler|sie|diese|die bugs)|repair those issues|apply the fixes)(?: fixen)?(?: with tool use)?',text)) or bool(re.fullmatch(prefix+r'die bugs fixen',text))

def resolve_followup(state,workspace):
    state.bind(workspace)
    verified=[f for f in state.current_findings if f.status=='VERIFIED' and f.repair_targets]
    if not verified:
        return None,'Es gibt derzeit nur unbestätigte Verdachtsstellen. Ich kann sie zuerst verifizieren.' if state.current_findings else 'Kein aktueller verifizierter Befund in dieser Session. Bitte zuerst das Projekt prüfen oder konkrete Dateien und Änderungen nennen.',[]
    paths=list(dict.fromkeys(p for f in verified for p in f.repair_targets))[:3]
    for p in paths:
        path=workspace.path(p)
        if not path.is_file() or p not in state.file_hashes or file_hash(path)!=state.file_hashes[p]:
            return None,'Die betroffenen Dateien haben sich seit dem Befund geändert. Bitte zuerst erneut prüfen.',[]
    ids=[f.finding_id for f in verified if set(f.repair_targets)&set(paths)]
    evidence='\n'.join(f'{f.finding_id}: {f.summary}' for f in verified if f.finding_id in ids)
    task='Fix only '+', '.join('"'+p+'"' for p in paths)+'. Do not modify any other file. Do not change tests.\nVerified symptoms (the cause may still be unverified):\n'+evidence
    return task,'Verwende verifizierte Findings '+', '.join(ids)+' aus der vorherigen Projektprüfung.',ids

def findings_from_verification(result,workspace,task_id,number=1):
    from .intents import is_test_path
    if result.verification_status!='FAIL':return []
    post=result.post;items=[]
    errors=post.get('syntax_errors',[])
    for error in errors[:5]:
        p=error['file'];workspace.path(p)
        items.append(Finding(f'BUG-{number+len(items):03}',f'{p}:{error.get("line")}: {error["type"]}: {error["message"][:240]}',[p],'PYTHON_AST',result.verification_id,'VERIFIED',task_id,result.verifier_type,'FAIL','VERIFIED','VERIFIED',[p] if not is_test_path(p) else []))
    if items:return items
    checks=post.get('checks',[post])
    for check in checks[:8]:
        text=(check.get('stderr','')+'\n'+check.get('stdout',''))[:16384]
        matches=re.findall(r'res://([^\s:)]+\.(?:gd|tscn|tres)):(\d+)',text)
        for p,line in matches[:5]:
            if p.startswith('.alienape-verification/'):continue
            try:safe=workspace.path(p)
            except (PermissionError,ValueError):continue
            if not safe.is_file():continue
            summary=f'{p}:{line}: Godot meldet einen Parser-/Scriptfehler. Ursache nur soweit durch diese Diagnose belegt.'
            if any(p in f.files for f in items):continue
            items.append(Finding(f'BUG-{number+len(items):03}',summary,[p],'GODOT_DIAGNOSTIC',result.verification_id,'VERIFIED',task_id,result.verifier_type,'FAIL','VERIFIED','UNVERIFIED',[p]))
    if not items:
        paths=[p for p in result.files_verified if not p.startswith('.alienape-verification/')]
        if paths:
            for p in paths:workspace.path(p)
            targets=[p for p in paths if not is_test_path(p) and p.endswith(('.py','.gd'))]
            items.append(Finding(f'BUG-{number:03}','Ausgewählter objektiver Check meldet FAIL; genaue Ursache ist noch nicht verifiziert.',paths,'VERIFIER_FAIL',result.verification_id,'VERIFIED',task_id,result.verifier_type,'FAIL','VERIFIED','UNVERIFIED',targets))
    return items[:5]
