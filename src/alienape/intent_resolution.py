"""Language boundary only. Future resolvers implement the same narrow Protocol."""
from dataclasses import dataclass,replace
from enum import StrEnum
from typing import Protocol
import re

class IntentID(StrEnum):
    ANALYZE_DIRECTORY='ANALYZE_DIRECTORY';MODEL_STATUS_QUERY='MODEL_STATUS_QUERY';GENERAL_CHAT='GENERAL_CHAT';EXPLAIN_CODE='EXPLAIN_CODE';CODE_GENERATION_RESPONSE='CODE_GENERATION_RESPONSE';CAPABILITY_QUERY='CAPABILITY_QUERY'
    GREETING='GREETING';THANKS='THANKS';GOODBYE='GOODBYE';IDENTITY_QUERY='IDENTITY_QUERY'
    PROJECT_BUILD='PROJECT_BUILD';CREATE_PROJECT='CREATE_PROJECT';REASON_QUERY='REASON_QUERY';DELETE_REQUEST_UNSUPPORTED='DELETE_REQUEST_UNSUPPORTED'
    READ_FILE='READ_FILE';FIND_FILE='FIND_FILE';CREATE_FILE='CREATE_FILE';MODIFY_FILE='MODIFY_FILE'
    CREATE_DIRECTORY='CREATE_DIRECTORY';ANALYZE_FILE='ANALYZE_FILE';VERIFY_FILE='VERIFY_FILE';VERIFY_PROJECT='VERIFY_PROJECT'

@dataclass(frozen=True)
class IntentResolution:
    intent_id:IntentID=IntentID.GENERAL_CHAT
    confidence:float=0.0
    source:str='deterministic-v066'
    referenced_paths:tuple=()
    mutation_requested:bool=False
    project_scope:str='none'
    followup_reference:bool=False
    ambiguity_reason:str=''
    operations:tuple=()
    handled:bool=False
    missing_change_description:bool=False
    output_paths:tuple=()
    literal_content:str|None=None
    slots:object=None

class IntentResolver(Protocol):
    """Future SemanticIntentResolver maps to these IDs; controller execution stays unchanged."""
    def resolve(self,task:str)->IntentResolution:...

class DeterministicIntentResolver:
    """Bounded DE/EN patterns for observed requests, not a semantic classifier."""
    def resolve(self,task):
        from .project_request import project_request
        request=project_request(task)
        if request:
            from .task_slots import TaskSlots
            slots=TaskSlots(intent_id=IntentID.PROJECT_BUILD,project_goal=request.goal,project_root=request.root or '',language_hint=request.language_hint,new_project=True,mutation_requested=True,missing_slots=() if request.root else ('project_root',))
            return IntentResolution(IntentID.PROJECT_BUILD,1.,source='deterministic-project',handled=True,mutation_requested=True,project_scope='workspace',slots=slots)
        from .task_slots import resolve_slots
        return resolve_slots(task,self._legacy_resolve(task))

    def _legacy_resolve(self,task):
        from .routing import referenced_paths
        from .file_actions import direct_read_path
        from .intents import information_need
        I=IntentID;text=' '.join(task.strip().split());lower=text.casefold().rstrip('.!?').strip()
        paths=tuple(referenced_paths(task))
        if not paths:
            paths=tuple(re.findall(r'(?<!\w)(\.env(?:\.[\w-]+)?)(?!\w)',task,re.I))[:3]
        def result(intent,**kw):
            if kw.get('mutation_requested'):
                clauses=re.split(r'[.!?;]\s+|\n',task)
                only=[c for c in clauses if re.search(r'\b(?:change|modify|edit|update|fix|replace|write|ändere|ersetze|schreibe)\s+(?:only|nur)\b',c,re.I)]
                selected=tuple(p for c in only for p in referenced_paths(c))
                kw['output_paths']=(selected or paths) if intent==I.MODIFY_FILE else paths
            return IntentResolution(intent,1.0,referenced_paths=paths,handled=True,**kw)
        # Narrow multilingual capability questions are controller facts, not model guesses.
        if re.fullmatch(r'(?:was kannst du mit dateien machen|kannst du dateien löschen|what can you do with files|can you delete files|que peux-tu faire avec les fichiers|peux-tu supprimer des fichiers)',lower):
            return result(I.CAPABILITY_QUERY)
        french_directory=re.fullmatch(r'(?:crée|cree|créer|creer) un dossier nommé ([\w-]+)',text.rstrip('.!?'),re.I)
        if french_directory:
            paths=(french_directory.group(1),)
            return result(I.CREATE_DIRECTORY,mutation_requested=True,project_scope='files',source='deterministic-i18n-directory')
        # This intent describes an unsupported request; it never authorizes deletion.
        deletion=re.match(r'^(?:(?:bitte|please)\s+)?(?:lösche|loesche|löschen|loeschen|entferne|entfernen|delete|delet|remove)\b\s+(.+)$',text,re.I)
        if deletion:
            tail=deletion.group(1).strip().rstrip('!?')
            targets=referenced_paths(tail)
            if len(targets)==1:
                candidate=targets[0]
                quoted=r'(?:"'+re.escape(candidate)+r'"|\x27'+re.escape(candidate)+r'\x27|`'+re.escape(candidate)+r'`|„'+re.escape(candidate)+r'“|'+re.escape(candidate)+r')'
                exact=r'(?:(?:die|den|das|the)\s+)?(?:(?:datei|file)\s+)?'+quoted+r'[.!?]?'
                if re.fullmatch(exact,tail,re.I):
                    # Targets are diagnostic data only. Do not resolve or open them.
                    parts=candidate.replace('\\','/').split('/')
                    safe=not candidate.startswith(('/','\\')) and not re.match(r'^[A-Za-z]:',candidate) and not any(part in {'.','..'} for part in parts)
                    paths=(candidate,) if safe else ()
                    return result(I.DELETE_REQUEST_UNSUPPORTED,project_scope='files',source='deterministic-v0682')
            if re.fullmatch(r'(?:everything|evrything|alles|all files|alle dateien)',tail,re.I):
                return result(I.DELETE_REQUEST_UNSUPPORTED,project_scope='files',source='deterministic-v0682')
            explicit_content=bool(re.search(r'(?i)\bnicht\s+die\s+datei\b',tail) and re.search(r'(?i)\bnur\s+(?:den\s+)?(?:text|inhalt)\b',tail) and re.search(r'(?i)\bschreib\w*\s+stattdessen\s+\S',tail))
            if explicit_content and len(targets)==1:
                candidate=targets[0]
                if not candidate.startswith(('/', '\\')) and not re.match(r'^[A-Za-z]:',candidate) and not any(part in {'.','..'} for part in candidate.replace('\\','/').split('/')):
                    return IntentResolution(I.MODIFY_FILE,1.,source='deterministic-v0694',handled=True,referenced_paths=(candidate,),output_paths=(candidate,),mutation_requested=True,project_scope='files')
            content_request=bool(re.match(r'(?:(?:den|das|the)\s+)?(?:text|word|wort|inhalt|content|zeile|line|code)\b',tail,re.I))
            safe_targets=tuple(p for p in targets if not p.startswith(('/', '\\')) and not re.match(r'^[A-Za-z]:',p) and not any(part in {'.','..'} for part in p.replace('\\','/').split('/')))
            return IntentResolution(I.MODIFY_FILE if content_request else I.DELETE_REQUEST_UNSUPPORTED,1.0,source='deterministic-v0682',handled=True,referenced_paths=safe_targets if len(safe_targets)==1 else (),
                ambiguity_reason='Meinst du das Löschen der ganzen Datei oder eine Änderung ihres Inhalts? Bitte konkretisieren.')
        # L0 accepts complete, deliberately small interactions, never a request prefix.
        if re.fullmatch(r'(?:hallo|hi|hey|hello|guten morgen|good morning)(?:[,! ]+(?:wie geht(?:s|\x27s| es)(?: dir)?|how are you))?(?:\s*:[p)d])?',lower):return result(I.GREETING)
        if lower in {'danke','vielen dank','dankeschön','thanks','thank you','thx'}:return result(I.THANKS)
        if lower in {'tschüss','tschuess','auf wiedersehen','bis später','bye','goodbye'}:return result(I.GOODBYE)
        if re.fullmatch(r'(?:welches? (?:modell|model) (?:benutze|nutze|verwende) ich(?: gerade| für (?:meine|die) aufgabe)?|what model (?:do i use(?: for my task)?|am i using))',lower):return result(I.MODEL_STATUS_QUERY)
        if lower in {'wer bist du','was bist du','who are you'}:return result(I.IDENTITY_QUERY)
        if lower in {'bei was','warum','wieso nicht','what do you mean','why'}:return result(I.REASON_QUERY)
        if re.fullmatch(r'(?:was kannst du (?:wirklich )?mit dateien machen|welche datei[ -]?funktionen hast du|can you (?:create folders|delete files))',lower):return result(I.CAPABILITY_QUERY)
        capability=r'(?:was kannst du(?: alles)?(?: machen)?|(?:kannst|kanst) du (?:dateien (?:erstellen|ändern)|tools (?:verwenden|benutzen))|what can you do|can you (?:create|edit) files|can you use tools)'
        permission_statement=r'(?:es ist (?:eingestellt|eingestelt)[, ]+(?:dass|das) du berechtigung hast|you (?:already )?have permission)'
        if re.fullmatch(capability+'|'+permission_statement,lower):return result(I.CAPABILITY_QUERY)
        if re.fullmatch(r'(?:was für|welche|what) (?:programmier)?sprachen (?:kannst|beherrschst|can) (?:du|you)(?: schreiben)?',lower):return result(I.CAPABILITY_QUERY)
        file_noun=bool(re.search(r'\b(?:text ?datei?|text file|python (?:programm?|program|datei|file))\b',lower))
        if re.match(r'^(?:(?:bitte|please)\s+)?(?:schreib(?:e)?|write)\b',lower) and re.search(r'\b(?:funktion|function)\b',lower):
            if paths and re.search(r'\b(?:in|into|to)\s+',lower):return result(I.CREATE_FILE,mutation_requested=True,project_scope='files')
            if not paths:return result(I.CODE_GENERATION_RESPONSE)
        knowledge=re.match(r'^(?:wie (?:schreibe|erstelle|programm?iere) ich|how (?:do i|to)|(?:erklär|erkläre|explain)(?: mir)? (?:wie\b|ein\w*|a|an|python)\b)',lower)
        example=not file_noun and re.match(r'^(?:schreib(?:e)? mir|write(?: me)?|show me)\b.+\b(?:in|with|using) python\b',lower)
        if (knowledge or example) and not paths:
            return result(I.EXPLAIN_CODE)
        if re.search(r'\bread[ -]only\b|\bonly (?:analyse|analyze|inspect)\b|nur (?:analysieren|lesen)|ändere nichts|do not (?:write|change|modify|edit)\b',lower):
            return IntentResolution(referenced_paths=paths)
        if information_need(task)=='VERIFY_EXISTING_PROJECT':return result(I.VERIFY_PROJECT,project_scope='workspace')
        prefix=r'(?:(?:bitte|please)\s+|(?:kannst|kanst) du (?:mir )?(?:bitte )?|can you (?:please )?)?'
        # A short acknowledgement may precede the explicit request, never file contents.
        action=re.sub(r'^(?:auch gut|ok|okay|gut)[.!]\s*','',lower)
        action=re.sub(r'^(?:hallo|hi|hey|hello)[,!]?\s+(?=(?:kanst|kannst|can|bitte|please|erstel|mach|create|make))','',action)
        create=bool(re.match(r'^'+prefix+r'(?:erstell\w*|erstel\w*|schreib\w*|create|write|mach\w*|make)\b',action)) or bool(re.match(r'^'+prefix+r'.{0,70}\b(?:python program\w*|text ?datei)\b.{0,30}\b(?:machen|erstel\w*)\b',action))
        create=create or bool(re.match(r'^'+prefix+r'(?:ein|eine|einen)\s+.{0,100}\b(?:anlegen|erstel\w*|machen)\b',action))
        if re.match(r'^'+prefix+r'(?:write|schreib\w*)\b',action) and not file_noun and not re.search(r'\b(?:new|neu\w*|containing)\b',lower):create=False
        directory=bool(re.search(r'\b(?:ordner|directory|folder)\b',lower))
        from .create_syntax import file_arguments,directory_argument,content_clause
        header,_=content_clause(task)
        if create and re.search(r'\bgodot\s+proje[ck]t\b',header,re.I):
            paths=();return result(I.CREATE_PROJECT,project_scope='workspace')
        from .create_syntax import action_text,VERB
        if create and re.match(r'^'+prefix+VERB+r'\s+(?:mir\s+)?(?:(?:ein(?:en?)?|a|the)\s+)?(?:ordner|directory|folder)\b',action_text(header),re.I):
            name=directory_argument(task);paths=(name,) if name else ()
            return result(I.CREATE_DIRECTORY,mutation_requested=True,project_scope='files',ambiguity_reason='' if name else 'Bitte einen eindeutigen Ordnernamen nennen; es wurde kein Ordner erstellt.')
        if create and (paths or file_noun):
            paths,literal,error=file_arguments(task,python='python' in header.casefold())
            return result(I.CREATE_FILE,mutation_requested=True,project_scope='files',literal_content=literal,ambiguity_reason=error,operations=(I.CREATE_FILE,I.VERIFY_FILE) if re.search(r'\b(?:prüfe|teste|verify|check|test)\b',lower) else (I.CREATE_FILE,))
        direct=direct_read_path(task)
        if direct is not None:
            paths=(direct,);return result(I.READ_FILE,project_scope='files',operations=(I.READ_FILE,))
        pronoun=not paths and bool(re.fullmatch(prefix+r'(?:prüfe|pruefe|teste|check|verify|analysiere|analyze|ändere|aendere|change|modify|erklär(?:e)?(?: mir)?|explain(?: to me)?|hilf mir|help me(?: with)?)(?: (?:es|das|sie|die datei|das script|it|that|the file|the script|dabei))(?: (?:auf bugs|auf fehler|for bugs|for errors))?',lower))
        finding=bool(re.match(r'^'+prefix+r'(?:finde|find|suche|locate)\b',action))
        if re.match(r'^'+prefix+r'(?:finde|find)\s+(?:(?:den|the|a)\s+)?(?:fehler|bug|cause)\s',action):finding=False
        verify=bool(re.search(r'\b(?:prüfe|pruefe|teste|check|verify|test)\b',lower))
        analyze=bool(re.search(r'\b(?:analysiere|analyze|analyse|inspect|review)\b',lower))
        explain=bool(re.match(r'^'+prefix+r'(?:erklär\w*|explain|hilf mir|help me)\b',action))
        modify=bool(re.match(r'^'+prefix+r'(?:ändere|aendere|change|modify|edit|fix|repariere)\b',action))
        if paths or pronoun:
            common={'project_scope':'files','followup_reference':pronoun}
            if modify:
                return result(I.MODIFY_FILE,mutation_requested=True,missing_change_description=pronoun,operations=((I.FIND_FILE,I.MODIFY_FILE) if finding else (I.MODIFY_FILE,)),**common)
            if verify:
                from .contracts import requests_json
                kind=I.ANALYZE_FILE if requests_json(task) else I.VERIFY_FILE
                return result(kind,operations=(I.READ_FILE,kind),**common)
            if analyze:return result(I.ANALYZE_FILE,operations=(I.READ_FILE,I.ANALYZE_FILE),**common)
            if explain:return result(I.EXPLAIN_CODE,operations=(I.READ_FILE,I.EXPLAIN_CODE),**common)
            if finding:
                target=I.MODIFY_FILE if re.search(r'\b(?:ändere|modify|edit|fix)\b',lower) else I.READ_FILE if re.search(r'\b(?:lies|read)\b',lower) else I.FIND_FILE
                return result(target,mutation_requested=target==I.MODIFY_FILE,operations=(I.FIND_FILE,target),**common)
        return IntentResolution(referenced_paths=paths,project_scope='files' if paths else 'none')

def bind_recent(resolution,state,workspace):
    """Operational reference binding, no language parsing and no history inspection."""
    if not resolution.followup_reference:return resolution
    state.bind(workspace)
    candidates=[]
    for p in state.last_inspected_files:
        try:
            path=workspace.path(p)
            if path.is_file() and p not in candidates:candidates.append(p)
        except (OSError,ValueError):continue
    if len(candidates)!=1:
        return replace(resolution,ambiguity_reason='Mehrere mögliche Dateien; bitte den Zielpfad nennen.' if candidates else 'Keine eindeutige aktuelle Datei; bitte den Zielpfad nennen.')
    return replace(resolution,referenced_paths=tuple(candidates))
