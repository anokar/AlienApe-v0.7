"""Small, explicit output contracts and write scopes; no model-driven policy."""
from dataclasses import dataclass, field
from enum import StrEnum
import json
import os
import re
from .completion import CompletionRejected, FailureType


class ResponseMode(StrEnum):
    PLAIN_TEXT='PLAIN_TEXT'
    STRUCTURED_JSON='STRUCTURED_JSON'
    FILE_PROPOSAL='FILE_PROPOSAL'
    FILE_CONTENT='FILE_CONTENT'
    TOOL_ACTION='TOOL_ACTION'
    ANALYSIS_RESULT='ANALYSIS_RESULT'


@dataclass
class WriteScope:
    read_only: bool=True
    allowed_paths: list[str]=field(default_factory=list)
    deny_other_paths: bool=True
    allow_new_file: bool=False
    allow_directory: bool=False
    protect_tests: bool=False
    pending_source_targets: bool=False
    planned_new_paths: list[str]=field(default_factory=list)

    @classmethod
    def for_task(cls,task):
        from .routing import referenced_paths
        from .intents import information_need
        if information_need(task)=='VERIFY_EXISTING_PROJECT':return cls()
        # Protecting tests/OTHER files is a scoped restriction, not a global veto.
        protect_tests=bool(re.search(r'(?:do not|don.t)\s+(?:change|modify|edit|write|delete|remove)\s+(?:the\s+)?tests?\b|(?:keine\s+tests?\s+ändern|tests?\s+nicht\s+ändern)',task,re.I))
        text=re.sub(r'(?:do not|don.t)\s+\w+\s+(?:(?:any|the)\s+)?other\s+files?|(?:do not|don.t)\s+\w+\s+(?:the\s+)?tests?\b|keine\s+andere[nr]?\s+Datei(?:en)?|no\s+other\s+files?|keine\s+tests?\s+ändern|tests?\s+nicht\s+ändern','',task,flags=re.I)
        prohibited=bool(re.search(r'ändere\s+(?:nichts|keine\s+Datei)|keine\s+Dateien?\s+(?:verändern|ändern|schreiben)|(?:analysiere|analysieren|analyse|analyze)\s+nur|nur\s+(?:analysieren|lesen)|\bread[ -]only\b|\bonly\s+(?:analyse|analyze|inspect)\b|(?:do not|don.t)\s+(?:write|change|modify|edit|update|fix|repair|refactor|add|implement|create|replace|rename|remove|delete)\b',text,re.I))
        verb=r'(?:ändere|ändern|verändere|ersetze|ersetz\w*|schreib\w*|erstell\w*|korrigier\w*|reparier\w*|refaktorier\w*|ergänz\w*|erweiter\w*|implementier\w*|lösch\w*|entfern\w*|benenne|change|modify|edit|update|fix|repair|refactor|add|implement|create|write|replace|rename|remove|delete|debug(?=\s+(?:the\s+)?failing\s+tests)|make\s+(?:a\s+)?new)'
        edit=bool(re.search(r'(?:^|[\n,.;!?]\s*|\b(?:und|and|bitte|please|dann)\s+)'+verb+r'\b\s+\S',text,re.I))
        if prohibited or not edit:return cls()
        paths=referenced_paths(task)
        if not paths and not re.search(r'\b(?:implementation|code|source|support|function|method|feature|module|test\w*|package|class|file|script|document|config\w*|project|directory|folder|implementierung|funktion|datei\w*|ordner|skript|dokument|modul|konfiguration)\b',task,re.I):return cls()
        directory=bool(re.search(r'\b(?:ordner|directory|folder)\b',task,re.I))
        if directory and not paths:
            match=re.search(r'\b(?:ordner|directory|folder)\s+["`]?([\w/\\.-]+)',task,re.I)
            if match:paths=[match[1].rstrip('.')]
        # Explicit "only" clauses take precedence over other referenced inputs.
        clauses=re.split(r'[.!?;]\s+|\n',task)
        only=[clause for clause in clauses if re.search(r'\b(?:change|modify|edit|update|fix|replace|write|ändere|ersetze|schreibe)\s+(?:only|nur|at most these two files)\b',clause,re.I)]
        only_paths={p.casefold() for clause in only for p in referenced_paths(clause)}
        restricted=[p for p in paths if p.casefold() in only_paths]
        targeted=[]
        for path in paths:
            for match in re.finditer(re.escape(path),task,re.I):
                prefix=task[max(0,match.start()-70):match.start()]
                if re.search(verb+r'\b[^;\n]*$',prefix,re.I) or re.search(r'\bin\s*["`]?$',prefix,re.I):
                    targeted.append(path);break
        paths=restricted or targeted or paths
        if protect_tests:
            from .intents import is_test_path
            paths=[p for p in paths if not is_test_path(p)]
        create=bool(re.search(r'(?:^|[\n,.;!?]\s*|\b(?:and|und|please|bitte)\s+)(?:create|write|erstell\w*|schreib\w*|make\s+(?:a\s+)?new)\b',text,re.I))
        nouns=bool(re.search(r'\b(?:modules?|tests?|packages?|class file|source file|scripts?|documents?|configs?|configuration|datei\w*|modul\w*|paket\w*|testdatei\w*|dokument\w*|skript\w*|konfiguration\w*)\b',task,re.I))
        planned=[]
        if create and nouns and not paths and not directory:
            if re.search(r'\bpython\b',task,re.I):
                match=re.search(r'\bimplementing\s+([a-zA-Z_]\w*)|\b(?:function|funktion)\s+([a-zA-Z_]\w*)',task,re.I)
                stem=(next(g for g in match.groups() if g) if match else 'module')[:48].lower()
                planned=[stem+'.py']
                if re.search(r'\btests?\b',task,re.I):planned.append('tests/test_'+stem+'.py')
            else:planned=['config.json' if re.search(r'\bconfig(?:uration)?\b',task,re.I) else 'document.txt']
            paths=list(planned)
        from .intents import information_need
        pending=not paths and not directory and not create and (information_need(task) in {'DEBUG_EXISTING_CODE','REFACTOR_EXISTING_CODE','MODIFY_EXISTING_CODE'} or bool(re.search(r'\b(?:implementation|existing code|implementierung)\b',task,re.I)))
        return cls(False,paths,True,create,directory,protect_tests,pending,planned)

    def resolve_sources(self,files):
        from .intents import SOURCE_SUFFIXES,is_test_path
        if self.pending_source_targets:
            self.allowed_paths=[f['path'] for f in files if os.path.splitext(f['path'])[1].lower() in SOURCE_SUFFIXES and not is_test_path(f['path'])]
            self.pending_source_targets=False

    def permits(self,path,workspace,*,directory=False):
        safe=workspace.path(path)
        key=os.path.normcase(safe.relative_to(workspace.root).as_posix())
        allowed={os.path.normcase(workspace.path(p).relative_to(workspace.root).as_posix()) for p in self.allowed_paths}
        if directory and self.allow_new_file:
            allowed={os.path.normcase(parent.relative_to(workspace.root).as_posix()) for p in self.allowed_paths for parent in workspace.path(p).parents if parent!=workspace.root and parent.is_relative_to(workspace.root)}
        elif directory and not self.allow_directory:return False
        from .intents import is_test_path
        return not self.read_only and not (self.protect_tests and is_test_path(key)) and key in allowed


@dataclass
class ResponseContract:
    mode: ResponseMode=ResponseMode.PLAIN_TEXT
    expects_json: bool=False
    schema: dict|None=None
    required_keys: list[str]=field(default_factory=list)
    allows_markdown_fence: bool=True
    expects_file_proposal: bool=False
    expects_plain_text: bool=True
    validator_id: str|None=None


def requests_json(task):
    # Output imperatives only; mentioning/reading/explaining JSON is not a contract.
    return bool(re.search(r'(?:antworte|antwort\w*|ausgabe|ausgeben|zurückgeben|gib|liefere|erzeuge|generiere|return|respond|reply|output|format)\b[^.!?\n]{0,170}\bjson(?:-objekt|-object|-datenobjekt)?\b',task,re.I))


def contract_for_task(task,validator=None):
    from .validators import JSONValidator
    from .discovery import workspace_intent
    scope=WriteScope.for_task(task)
    schema=getattr(validator,'schema',None)
    expected=getattr(validator,'expected',None)
    keys=list(expected) if isinstance(expected,dict) else list((schema or {}).get('required',[]))
    wants=requests_json(task) or isinstance(validator,JSONValidator)
    if workspace_intent(task)=='listing' and not wants:return ResponseContract(validator_id=type(validator).__name__ if validator else None)
    tool_action=bool(re.search(r'\b(?:read_file|search_files|list_files)\b',task,re.I) or
        (re.search(r'\b(?:liste|auflisten|suche|search|list)\b',task,re.I) and re.search(r'\b(?:datei\w*|ordner|directory|workspace|verzeichnis|files?|folder)\b',task,re.I)))
    mode=ResponseMode.STRUCTURED_JSON if wants else ResponseMode.FILE_PROPOSAL if not scope.read_only else ResponseMode.ANALYSIS_RESULT if re.search(r'\b(?:analys\w*|review|prüfe|untersuch\w*)\b',task,re.I) else ResponseMode.TOOL_ACTION if tool_action else ResponseMode.PLAIN_TEXT
    return ResponseContract(mode,wants,schema,keys,True,mode==ResponseMode.FILE_PROPOSAL,not wants and mode!=ResponseMode.FILE_PROPOSAL,
                            type(validator).__name__ if validator else 'JSONValidator' if wants else None)


@dataclass
class NormalizedResponse:
    payload: str
    proposed_files: list=field(default_factory=list)
    status: str|None=None
    protocol_normalized: bool=False
    normalizations: list[str]=field(default_factory=list)


def remove_json_fence(text):
    match=re.fullmatch(r'\s*```json[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t\r\n]*',text,re.I)
    if match:
        try:json.loads(match[1])
        except ValueError:pass
        else:return match[1],True
    return text,False


def normalize_response(text,contract,*,allow_proposals=False):
    result=NormalizedResponse(text)
    if contract.allows_markdown_fence:
        result.payload,removed=remove_json_fence(text)
        if removed:result.normalizations.append('MARKDOWN_JSON_FENCE_REMOVED')
    def unique_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    try:value=json.loads(result.payload,object_pairs_hook=unique_pairs if allow_proposals and contract.expects_file_proposal else dict)
    except ValueError:
        if allow_proposals and contract.expects_file_proposal:
            raise CompletionRejected(FailureType.FORMAT_FAILURE,'FILE_PROPOSAL erfordert ein vollständiges JSON-Objekt; keine Textzusätze.')
        result.protocol_normalized=bool(result.normalizations)
        return result
    if not isinstance(value,dict):
        result.protocol_normalized=bool(result.normalizations)
        return result
    # An explicitly requested domain key is payload, even if named "answer"/"status".
    reserved={'answer','payload','status','proposed_files','metadata','reason'}
    explicit_payload=bool(contract.required_keys) and set(contract.required_keys)<=set(value)
    envelope=not explicit_payload and (
        'payload' in value or 'proposed_files' in value or
        ('answer' in value and set(value)<=reserved) or
        value.get('status') in ('cannot_complete','verification_failed'))
    if envelope:
        def reject(message):raise CompletionRejected(FailureType.FORMAT_FAILURE,'AgentEnvelope: '+message)
        if set(value)-reserved or ('answer' in value and 'payload' in value):reject('uneindeutige Nutzdaten oder unbekannte Felder')
        if 'status' in value and value['status'] not in ('ok','done','cannot_complete','verification_failed'):reject('ungültiger Status')
        if 'metadata' in value and not isinstance(value['metadata'],dict):reject('metadata muss ein Objekt sein')
        if 'reason' in value and not isinstance(value['reason'],str):reject('reason muss Text sein')
        if 'payload' not in value and 'answer' not in value:reject('Nutzdaten fehlen')
        proposals=value.get('proposed_files',[])
        if not isinstance(proposals,list) or len(proposals)>3:reject('ungültige proposed_files')
        for p in proposals:
            if not isinstance(p,dict) or set(p)!={'path','content'} or not isinstance(p['path'],str) or not p['path'] or not isinstance(p['content'],str) or len(p['content'])>262144:reject('ungültiger Dateivorschlag')
        payload=value.get('payload',value.get('answer'))
        if not contract.expects_json and not isinstance(payload,str):reject('Text-Nutzdaten erwartet')
        result.payload=payload if isinstance(payload,str) else json.dumps(payload,ensure_ascii=False)
        result.proposed_files=proposals
        result.status=value.get('status')
        result.normalizations.append('AGENT_ENVELOPE_PAYLOAD_EXTRACTED')
    if allow_proposals and contract.expects_file_proposal and not result.proposed_files and result.status not in ('cannot_complete','verification_failed'):
        raise CompletionRejected(FailureType.FORMAT_FAILURE,'FILE_PROPOSAL benötigt proposed_files mit path/content.')
    result.protocol_normalized=bool(result.normalizations)
    return result
