"""Small deterministic slots and one-turn continuity. No tools or permissions here."""
from dataclasses import dataclass,field,replace,asdict
from enum import StrEnum
import hashlib,re,time
from .intent_resolution import IntentID as I,IntentResolution
from .security import redactor,transport_redactor

MAX_TEXT=8192
MAX_ARTIFACT=32768

class ContentMode(StrEnum):
    EMPTY='EMPTY';LITERAL='LITERAL';GENERATE='GENERATE';EXISTING='EXISTING';UNKNOWN='UNKNOWN'

@dataclass(frozen=True)
class TaskSlots:
    intent_id:str='GENERAL_CHAT'
    target_paths:tuple=()
    source_paths:tuple=()
    content_mode:str='UNKNOWN'
    literal_content:str|None=None
    generation_request:str=''
    project_type:str=''
    language_hint:str=''
    mutation_requested:bool=False
    missing_slots:tuple=()
    ambiguous_slots:tuple=()
    resolution_source:str='DIRECT_RULE'
    slot_sources:dict=field(default_factory=dict)
    project_goal:str=''
    project_root:str=''
    new_project:bool=False

    @property
    def complete(self):return not self.missing_slots and not self.ambiguous_slots

@dataclass(frozen=True)
class PendingTaskState:
    slots:TaskSlots
    originating_turn_id:str
    originating_turn:int
    created_at:float

@dataclass(frozen=True)
class PendingChoice:
    target_path:str
    originating_turn:int
    created_at:float

@dataclass(frozen=True)
class LastGeneratedArtifact:
    type:str
    language:str
    content:str
    source_turn_id:str
    turn:int
    created_at:float

    def metadata(self):return {'present':True,'type':self.type,'language':self.language,'source_turn_id':self.source_turn_id,'bytes':len(self.content.encode()),'content_sha256':hashlib.sha256(self.content.encode()).hexdigest()}

FOLLOWUPS={'was','warum','wieso','wieso nicht','bei was','was meinst du','what','why','what do you mean'}
CODE_SUFFIXES={'.zig':'zig','.c':'c','.py':'python','.cpp':'cpp','.cc':'cpp','.cxx':'cpp','.html':'html','.htm':'html','.gd':'gdscript','.js':'javascript','.ts':'typescript'}

def short_question(task):return ' '.join(task.casefold().strip().rstrip('?!.,').split()) in FOLLOWUPS

def language_hint(task,paths=()):
    for p in paths:
        suffix='.'+p.rsplit('.',1)[-1].casefold() if '.' in p else ''
        if suffix in CODE_SUFFIXES:return CODE_SUFFIXES[suffix]
    text=task.casefold()
    return next((lang for pattern,lang in [(r'(?<!\w)\.py\s+datei\b','python'),(r'\bpython\b','python'),(r'(?:c\+\+|\bcpp\b)','cpp'),(r'\bC\s+(?:funktion|function)\b','c'),(r'\bzig\b','zig'),(r'\bhtml\b','html'),(r'\b(?:gdscript|godot)\b','gdscript')] if re.search(pattern,text)),'')


def lexical_paths(text):
    from .routing import referenced_paths
    paths=referenced_paths(text)
    if not paths:paths=re.findall(r'(?<!\w)(\.env(?:\.[\w-]+)?|[\w./-]+\.zig)(?!\w)',text,re.I)
    # Header-only explicit named files, including extensionless targets.
    named=re.search(r'\b(?:namens|named|nommé|nomme|als|as)\s+("[^"\n]+"|\x27[^\x27\n]+\x27|`[^`\n]+`|[\w./\\:-]+)(?=\s*(?:$|erstell\w*|anlegen))',text,re.I)
    if named and not paths:paths=[named[1].strip('"\x27`')]
    return tuple(dict.fromkeys(paths))[:4]


def create_parts(task):
    from .create_syntax import content_clause
    # French is a small lexical payload boundary, not a generative translation.
    match=re.search(r'\b(?:avec\s+le\s+texte|avec\s+le\s+contenu)\s+',task,re.I)
    header,body=(task[:match.start()],task[match.end():]) if match else content_clause(task)
    # Suffix infinitive is outside a literal's closing quote, never part of content.
    if body is not None:body=re.sub(r'\s+(?:erstel\w*|anlegen|create)\s*[.!?]?\s*$','',body,flags=re.I)
    return header.strip(),body


def file_output_before_verb(header):
    """An explicit file/page object followed by create, before any behavior."""
    from .create_syntax import PREFIX,action_text
    pattern=(PREFIX+r'(?:(?:ein\w*|a|an)\s+)?(?:(?:\.py|python|html|text)\s+)?'
             r'(?:datei\w*|seite|file|page)\s+(?:erstel\w*|anlegen|create)\b')
    return bool(re.match(pattern,action_text(header),re.I))


def explicit_create(task):
    header,_=create_parts(task)
    from .create_syntax import action_text,PREFIX,VERB
    header=action_text(header)
    if re.search(r'\b(?:do not|don.t|nicht|keine|ne pas|n.écris pas|n.ecris pas)\b',header,re.I):return False
    return bool(file_output_before_verb(header) or re.match(r'^'+PREFIX+VERB+r'\b',header,re.I) or re.match(r'^(?:crée|créer|creer|créez|cree)\b',header,re.I)
        or re.match(r'^(?:(?:bitte|please)\s+)?(?:eine?|einen|a)\b',header,re.I) and re.search(r'\b(?:erstel\w*|anlegen|machen)\s*[.!?]?$',task,re.I))


def generation_text(task):
    """Preserve a natural request; normalize the explicit .py file description."""
    text=transport_redactor.text(task)[:MAX_TEXT]
    match=re.search(r'(?<!\w)\.py\s+datei\s+(die\s+.+)',text,re.I)
    if not match:return text
    description=re.sub(r'\bein\s+file\b','eine Datei',match.group(1),flags=re.I)
    return 'Python-Datei, '+description


def slots_for(task,legacy,*,suggested=None):
    """Slot values are extracted from raw text only, never semantic output."""
    from .create_syntax import parenthesized_write_clause
    clause=parenthesized_write_clause(task)
    header,body=(clause[0],None) if clause else create_parts(task)
    paths=lexical_paths(header)
    intent=I(suggested) if suggested is not None else legacy.intent_id
    # Preserve an explicitly named single directory across the language boundary.
    named=re.fullmatch(r'(?i)\s*(?:erstelle(?:\s+einen)?\s+ordner\s+namens|create\s+(?:a\s+)?folder\s+named)\s+([\w-]+)\s*[.!?]?\s*',task)
    if named or legacy.source=='deterministic-i18n-directory':
        name=named.group(1) if named else legacy.referenced_paths[0]
        return TaskSlots(I.CREATE_DIRECTORY,target_paths=(name,),mutation_requested=True,slot_sources={'target_path':'RAW_LEXICAL'})
    if intent==I.DELETE_REQUEST_UNSUPPORTED:
        paths=legacy.referenced_paths
        return TaskSlots(intent,target_paths=paths,mutation_requested=False,slot_sources={'target_path':'RAW_LEXICAL'} if paths else {})
    create=explicit_create(task) or (suggested is None and legacy.intent_id==I.CREATE_FILE and legacy.mutation_requested)
    from .create_syntax import PREFIX,VERB,action_text
    save=bool(re.search(r'\b(?:speicher\w*|save|write)\b',header,re.I) and re.search(r'\b(?:das|es|it|that|this|die funktion)\b',header,re.I))
    directory=bool(re.match(r'^'+PREFIX+VERB+r'\s+(?:mir\s+)?(?:(?:ein(?:en?)?|a|the)\s+)?(?:ordner|directory|folder)\b',action_text(header),re.I))
    literal_write=re.fullmatch(r'\s*(?:schreibe|write)\s+(["\x27`])([\s\S]*?)\1\s+(?:in|to)\s+(["\x27`])([^"\x27`\n]+)\3\s*[.!?]?\s*',task,re.I)
    if literal_write:
        return TaskSlots(I.CREATE_FILE,target_paths=(literal_write[4],),content_mode=ContentMode.LITERAL,literal_content=literal_write[2],mutation_requested=True,slot_sources={'target_path':'RAW_LEXICAL','literal_content':'RAW_LITERAL','content_mode':'RAW_LEXICAL'})
    code=language_hint(task,paths)
    file_noun=bool(re.search(r'\b(?:datei\w*|fichier|files?|program\w*|programm\w*|seiten?|pages?)\b',header,re.I) or re.search(r'python[ -]datei',header,re.I))
    # Preserve workspace generators and directory controller paths.
    if intent==I.CREATE_PROJECT or create and re.search(r'\bgodot\s+proje[ck]t\b',header,re.I):return TaskSlots(I.CREATE_PROJECT,project_type='godot',resolution_source='DIRECT_RULE')
    if directory or (suggested==I.CREATE_DIRECTORY and not file_output_before_verb(header)):
        from .create_syntax import directory_argument
        name=directory_argument(task)
        return TaskSlots(I.CREATE_DIRECTORY,target_paths=(name,) if name else (),mutation_requested=True,missing_slots=() if name else ('target_path',),slot_sources={'target_path':'RAW_LEXICAL'} if name else {})
    # Do not turn a read-only educational question into file creation.
    educational=legacy.intent_id in {I.EXPLAIN_CODE,I.CODE_GENERATION_RESPONSE} and not paths
    behavior=bool(file_noun and re.search(r'\b(?:die|das|that|which)\s+\S+\s+\S+',header,re.I))
    if suggested==I.CREATE_FILE or save or create and (paths or file_noun or body is not None or code) and not educational or intent==I.CREATE_FILE:
        intent=I.CREATE_FILE
        # Filename suffix after the generation description, explicitly marked as a name.
        if not paths and body is not None and not body.startswith(('"',"'",'`','„')):
            tail=re.search(r'\b(?:als|alls|as|in|namens|datein namen)\s+([\w./\\:-]+\.(?:py|cpp|html|txt|md|gd|js|ts))\s*[.!?]?$',body,re.I)
            if tail:paths=(tail[1],)
        missing=[];ambiguous=[];literal=None;mode=ContentMode.UNKNOWN;generation=''
        if not paths:missing.append('target_path')
        if len(paths)>1:
            # Explicit conjunction of named output files is still a finite scope.
            p1=re.escape(paths[0]);p2=re.escape(paths[1])
            multi=bool(len(paths)==2 and re.search(r'(?:create|erstel\w*|write|schreib\w*)\s+["`]?'+p1+r'["`]?\s+(?:and|und)\s+["`]?'+p2+r'["`]?',header,re.I))
            if not multi:ambiguous.append('target_path')
        if not (create or save):missing.append('explicit_mutation_request')
        location=re.search(r'\b(?:im|in|unter|inside)\s+(?:ordner|folder|directory)\s+([\w/\\.-]+)',header,re.I)
        if paths and location and location[1]!=paths[0]:ambiguous.append('target_path')
        empty=bool(re.search(r'\b(?:leere?|lere|empty|blank|vide)\b',header,re.I))
        if clause:
            # A preceding code-generation instruction conflicts with a literal payload.
            context=header
            for path in paths:context=context.replace(path,'')
            code_context=bool(re.search(r'\b(?:funktion|function|programm\w*|generier\w*|generate|code)\b|\b\w+\s*\([^)]*\)',context,re.I))
            _,prior_body=create_parts(header)
            if clause[2] or empty or code_context or prior_body is not None:ambiguous.append('content_mode')
            else:mode=ContentMode.LITERAL;literal=clause[1]
        elif empty and body is not None:ambiguous.append('content_mode')
        elif empty:mode=ContentMode.EMPTY;literal=''
        elif body is not None:
            from .create_syntax import literal_payload
            literal,error=literal_payload(body)
            explicit_text=bool(re.search(r'\b(?:text|inhalt|inhat|content|texte|contenu|sentence|satz)\s*[:.]?\s*$',header,re.I))
            # Header includes the marker before create_parts splits it, so inspect raw.
            explicit_text=explicit_text or bool(re.search(r'\b(?:mit\s+(?:dem\s+)?(?:text|inhalt|inhat)|with\s+(?:the\s+)?(?:text|content)|avec\s+le\s+(?:texte|contenu)|containing)\s',task,re.I))
            generation_words=bool(re.search(r'\b(?:programm?\w*|python|code|button|seite|page|function|funktion|generat\w*|generier\w*)\b',body,re.I))
            quoted=body.startswith(('"',"'",'`','„','“'))
            if error:ambiguous.append('content_mode');literal=None
            elif literal is not None and (quoted or explicit_text or not code and not generation_words):mode=ContentMode.LITERAL
            elif generation_words or code:mode=ContentMode.GENERATE;literal=None;generation=generation_text(task)
            elif literal is not None:mode=ContentMode.LITERAL
            else:missing.append('content')
        elif save and not create:mode=ContentMode.EXISTING
        elif code and re.fullmatch(r'(?i)\s*(?:erstelle|erstel|create|mach|make)\s+[\w./-]+\.(?:html?)\s*[.!?]?\s*',task):missing.append('content')
        elif code or behavior or re.search(r'\b(?:funktion|function)\b',task,re.I) or re.search(r'\b(?:generat\w*|generier\w*|hello[ -]?world|hallo welt)\b',task,re.I):mode=ContentMode.GENERATE;generation=generation_text(task)
        else:missing.append('content')
        if mode==ContentMode.UNKNOWN and 'content_mode' not in ambiguous and 'content' not in missing:missing.append('content')
        if literal is not None and len(literal.encode())>MAX_ARTIFACT:literal=None;mode=ContentMode.UNKNOWN;ambiguous.append('content')
        sources={'target_path':'RAW_LEXICAL'} if paths else {}
        if mode!=ContentMode.UNKNOWN:sources['content_mode']='RAW_LEXICAL'
        if literal is not None:sources['literal_content']='PARENTHESIZED_USER_PAYLOAD' if clause or (body is not None and body.startswith('(')) else 'RAW_LITERAL'
        if generation:sources['generation_request']='RAW_REQUEST_NORMALIZED' if generation!=transport_redactor.text(task)[:MAX_TEXT] else 'RAW_REQUEST'
        return TaskSlots(intent,paths,(),mode,literal,generation,language_hint=language_hint(task,paths),mutation_requested=bool(create or save),missing_slots=tuple(dict.fromkeys(missing)),ambiguous_slots=tuple(dict.fromkeys(ambiguous)),slot_sources=sources)
    return TaskSlots(intent,target_paths=legacy.output_paths,source_paths=legacy.referenced_paths,content_mode=ContentMode.EXISTING if legacy.referenced_paths else ContentMode.UNKNOWN,language_hint=code,mutation_requested=legacy.mutation_requested,missing_slots=(),ambiguous_slots=('reference',) if legacy.ambiguity_reason else (),slot_sources={'source_paths':'RAW_LEXICAL'} if legacy.referenced_paths else {})


def clarify(slots):
    if 'target_path' in slots.ambiguous_slots:return 'Bitte genau einen eindeutigen relativen Zielpfad nennen.'
    if 'target_path' in slots.missing_slots:return 'Wie soll die Datei heißen? Bitte einen eindeutigen relativen Dateipfad nennen.'
    if 'explicit_mutation_request' in slots.missing_slots:return 'Soll ich diese Datei wirklich neu erstellen? Bitte den Dateiauftrag ausdrücklich mit Ziel und Inhalt nennen.'
    if slots.content_mode==ContentMode.EXISTING:return 'Kein eindeutiges unmittelbar vorher erzeugtes Artefakt verfügbar. Bitte den zu speichernden Inhalt nennen.'
    return 'Welchen Inhalt soll die Datei haben: leer, ausdrücklich angegebener Text oder generierter Inhalt?'


def with_slots(legacy,slots):
    if slots.intent_id in {I.CREATE_FILE,I.CREATE_DIRECTORY,I.CREATE_PROJECT}:
        return replace(legacy,intent_id=I(slots.intent_id),handled=True,confidence=1.0 if slots.resolution_source in {'DIRECT_RULE','SESSION_PENDING','USER_CLARIFICATION'} else legacy.confidence,
            source='deterministic-slots-v068' if slots.resolution_source=='DIRECT_RULE' else slots.resolution_source,
            referenced_paths=slots.target_paths,output_paths=slots.target_paths,mutation_requested=slots.mutation_requested,
            literal_content=slots.literal_content,ambiguity_reason='' if slots.complete else clarify(slots),slots=slots,
            operations=(I(slots.intent_id),))
    return replace(legacy,slots=slots)


def resolve_slots(task,legacy):return with_slots(legacy,slots_for(task,legacy))


def continuity(state,task,resolution,workspace,task_id,emit):
    """Only the controller binds lexical slots to one immediately preceding turn."""
    now=time.monotonic();state.continuity_turn+=1;turn=state.continuity_turn
    pending=state.pending_task;artifact=state.last_generated_artifact;choice=state.pending_choice
    state.pending_task=None;state.last_generated_artifact=None;state.pending_choice=None
    if choice and turn==choice.originating_turn+1 and now-choice.created_at<=300:
        reply=' '.join(task.casefold().strip().rstrip('.!?').split())
        if reply in {'den inhalt','inhalt','inhalt ändern','den text','nur text','content','the content'}:
            emit('PENDING_STATE',{'action':'consumed','choice_type':'DELETE_OR_MODIFY','target_path':choice.target_path})
            pending_slots=TaskSlots(I.MODIFY_FILE,target_paths=(choice.target_path,),source_paths=(choice.target_path,),content_mode=ContentMode.EXISTING,mutation_requested=False,missing_slots=('change_description',),resolution_source='USER_CLARIFICATION')
            state.pending_task=PendingTaskState(pending_slots,task_id,turn,now)
            return IntentResolution(I.MODIFY_FILE,1.,source='USER_CLARIFICATION',handled=True,referenced_paths=(choice.target_path,),ambiguity_reason='Welche Änderung soll ich am Inhalt von '+choice.target_path+' vornehmen? Was soll stattdessen dort stehen? Es wurde nichts verändert.',slots=pending_slots),task
        if reply in {'ganze datei','die ganze datei','die datei','alles','datei','the whole file','whole file'}:
            emit('PENDING_STATE',{'action':'consumed','choice_type':'DELETE_OR_MODIFY','target_path':choice.target_path})
            return IntentResolution(I.DELETE_REQUEST_UNSUPPORTED,1.,source='USER_CLARIFICATION',handled=True,referenced_paths=(choice.target_path,)),task
        if reply in {'beides','weiß nicht','weiss nicht','both','ich weiß nicht','ja','nein','das','yes','no'}:
            return replace(resolution,handled=True,ambiguity_reason='Bitte wähle: ganze Datei löschen oder Inhalt ändern. Es wurde nichts verändert.'),task
        emit('PENDING_STATE',{'action':'cleared','reason':'UNRELATED_REPLY'})
    if resolution.source=='deterministic-v0682' and resolution.ambiguity_reason and len(resolution.referenced_paths)==1:
        target=resolution.referenced_paths[0]
        try:
            workspace.path(target)
            state.pending_choice=PendingChoice(target,turn,now)
            emit('PENDING_STATE',{'action':'created','choice_type':'DELETE_OR_MODIFY','target_path':target})
        except (ValueError,PermissionError):pass
    if pending and (now-pending.created_at>300 or turn!=pending.originating_turn+1):emit('PENDING_STATE',{'action':'cleared','reason':'EXPIRED'});pending=None
    if artifact and (now-artifact.created_at>300 or turn!=artifact.turn+1):emit('GENERATED_ARTIFACT',{'present':False,'reason':'EXPIRED'});artifact=None
    if short_question(task) and state.previous_failure_context:
        if pending:emit('PENDING_STATE',{'action':'cleared','reason':'ONE_TURN_CLARIFICATION'})
        return replace(resolution,intent_id=I.REASON_QUERY,handled=True,ambiguity_reason='',slots=TaskSlots(I.REASON_QUERY,resolution_source='USER_CLARIFICATION')),task
    slots=resolution.slots
    if re.fullmatch(r'(?:mach|mache|do|make)\s+(?:das|es|it|that)',task.strip().rstrip('.!?'),re.I):
        emit('PENDING_STATE',{'action':'cleared','reason':'AMBIGUOUS_REFERENCE'})
        return replace(resolution,handled=True,ambiguity_reason='Mehrdeutiger Bezug. Bitte Ziel und gewünschte Aktion ausdrücklich nennen.'),task
    if pending:
        if pending.slots.missing_slots==('content',) and not pending.slots.ambiguous_slots:
            short=task.casefold().strip().rstrip('.!?')
            literal=re.fullmatch(r'''(?s)\s*["“„]([^"“”]*)["“”]\s*[.!?]?\s*''',task)
            if short in {'leer','empty'} or literal:
                value=literal.group(1) if literal else ''
                slots=replace(pending.slots,content_mode=ContentMode.LITERAL if literal else ContentMode.EMPTY,literal_content=value,missing_slots=(),resolution_source='SESSION_PENDING',slot_sources={**pending.slots.slot_sources,'literal_content':'USER_CLARIFICATION'})
                emit('PENDING_STATE',{'action':'consumed','filled_slot':'content_mode'})
                return with_slots(resolution,slots),task
            if short in {'generiert','generated'}:
                slots=replace(pending.slots,content_mode=ContentMode.GENERATE,generation_request='Generiere passenden Inhalt für '+pending.slots.target_paths[0],missing_slots=(),resolution_source='SESSION_PENDING',slot_sources={**pending.slots.slot_sources,'generation_request':'USER_CLARIFICATION'})
                emit('PENDING_STATE',{'action':'consumed','filled_slot':'content_mode'})
                return with_slots(resolution,slots),'Create file "'+slots.target_paths[0]+'". Generate contents for: '+slots.generation_request
            if short in {'ausdrücklich','ausdruecklich','literal'}:
                state.pending_task=PendingTaskState(pending.slots,task_id,turn,now)
                return replace(resolution,handled=True,ambiguity_reason='Bitte gib den ausdrücklich gewünschten Text in Anführungszeichen an.'),task
        content_match=re.fullmatch(r'(?is)\s*(?:ausdrücklicher\s+text|ausdruecklicher\s+text|text|literal\s+text)\s+["“„]([^"“”]*)["“”]\s*[.!?]?\s*',task)
        if pending.slots.missing_slots==('content',) and not pending.slots.ambiguous_slots and content_match:
            slots=replace(pending.slots,content_mode=ContentMode.LITERAL,literal_content=content_match.group(1),missing_slots=(),resolution_source='SESSION_PENDING',slot_sources={**pending.slots.slot_sources,'literal_content':'USER_CLARIFICATION'})
            emit('PENDING_STATE',{'action':'consumed','filled_slot':'literal_content'})
            return with_slots(resolution,slots),task
        generation_match=re.fullmatch(r'(?is)\s*(?:generiere|generate)\s*:\s*(.{1,500})\s*',task)
        if pending.slots.missing_slots==('content',) and not pending.slots.ambiguous_slots and generation_match and not lexical_paths(generation_match.group(1)):
            slots=replace(pending.slots,content_mode=ContentMode.GENERATE,generation_request=generation_match.group(1).strip(),missing_slots=(),resolution_source='SESSION_PENDING',slot_sources={**pending.slots.slot_sources,'generation_request':'USER_CLARIFICATION'})
            emit('PENDING_STATE',{'action':'consumed','filled_slot':'generation_request'})
            return with_slots(resolution,slots),'Create file "'+slots.target_paths[0]+'". Generate contents for: '+slots.generation_request
        if pending.slots.missing_slots==('change_description',):
            m=re.fullmatch(r'(?is)\s*(?:ersetze(?:\s+den\s+inhalt)?\s+(?:durch|mit)|replace(?:\s+the\s+content)?\s+with)\s+["“„]([^"“”]*)["“”]\s*[.!?]?\s*',task)
            if m:
                emit('PENDING_STATE',{'action':'consumed','filled_slot':'change_description'})
                return IntentResolution(I.MODIFY_FILE,1.,source='SESSION_PENDING',handled=True,referenced_paths=pending.slots.target_paths,output_paths=pending.slots.target_paths,mutation_requested=True), 'Ändere '+pending.slots.target_paths[0]+' mit dem Inhalt "'+m.group(1)+'"'
        raw=task.strip().strip('"\x27`')
        simple=bool(re.fullmatch(r'[\w./\\ :+-]{1,200}',raw)) and len(lexical_paths(task))==1
        # Complete filename-only reply, not a verb or a second unrelated request.
        if simple and lexical_paths(task)==(raw,):
            workspace.path(raw)
            s=pending.slots
            if s.missing_slots==('target_path',) and not s.ambiguous_slots:
                slots=replace(s,target_paths=(raw,),missing_slots=(),resolution_source='SESSION_PENDING',slot_sources={**s.slot_sources,'target_path':'USER_CLARIFICATION'})
                resolution=with_slots(resolution,slots)
                task='Create file "'+raw+'". Generate contents for: '+s.generation_request if s.content_mode==ContentMode.GENERATE else task
                emit('PENDING_STATE',{'action':'consumed','originating_turn_id':pending.originating_turn_id,'filled_slot':'target_path'})
            else:emit('PENDING_STATE',{'action':'cleared','reason':'INCOMPATIBLE_REPLY'})
        elif re.fullmatch(r'(?:mach|mache|do|make)\s+(?:das|es|it|that)',task.strip().rstrip('.!?'),re.I):
            return replace(resolution,handled=True,ambiguity_reason='Mehrdeutiger Bezug. Bitte Ziel und gewünschte Aktion ausdrücklich nennen.'),task
        else:emit('PENDING_STATE',{'action':'cleared','reason':'UNRELATED_OR_AMBIGUOUS_REPLY'})
    slots=resolution.slots
    if slots and slots.intent_id==I.CREATE_FILE:
        if slots.content_mode==ContentMode.EXISTING:
            if artifact and slots.target_paths and slots.complete:
                slots=replace(slots,literal_content=artifact.content,language_hint=artifact.language,resolution_source='USER_CLARIFICATION',slot_sources={**slots.slot_sources,'literal_content':'LAST_GENERATED_ARTIFACT'})
                resolution=with_slots(resolution,slots);emit('GENERATED_ARTIFACT',artifact.metadata())
            else:
                slots=replace(slots,missing_slots=tuple(dict.fromkeys((*slots.missing_slots,'generated_artifact'))));resolution=with_slots(resolution,slots)
        if slots.missing_slots in {('target_path',),('content',)} and not slots.ambiguous_slots and slots.mutation_requested:
            safe=replace(slots,literal_content=transport_redactor.text(slots.literal_content) if slots.literal_content is not None else None,generation_request=transport_redactor.text(slots.generation_request)[:MAX_TEXT])
            state.pending_task=PendingTaskState(safe,task_id,turn,now);emit('PENDING_STATE',{'action':'created','missing_slots':slots.missing_slots,'originating_turn_id':task_id})
    elif artifact:emit('GENERATED_ARTIFACT',{'present':False,'reason':'UNRELATED_TURN'})
    return resolution,task


def remember_artifact(agent,answer):
    state=agent.session.task_state;r=agent.intent_resolution
    if not r or r.intent_id not in {I.EXPLAIN_CODE,I.CODE_GENERATION_RESPONSE} or agent.resources.counters.model_calls<=getattr(agent,'turn_model_calls_before',0) or r.ambiguity_reason or agent.last_failure_type.value not in {'SUCCESS','UNKNOWN'}:return
    value=getattr(agent,'last_response',{}).get('content') or answer
    match=re.search(r'```(python|py|cpp|c\+\+|c|html|gdscript|javascript|typescript)?\s*\n([\s\S]*?)\n?```',value,re.I)
    lang=language_hint(agent.current_task)
    if match:lang={'py':'python','c++':'cpp'}.get((match[1] or lang).casefold(),(match[1] or lang).casefold());value=match[2]
    elif not (lang=='python' and re.match(r'\s*(?:def |class |print\()',value) or lang=='html' and value.lstrip().startswith('<') or lang=='cpp' and '#include' in value or lang=='c' and re.match(r'\s*(?:(?:int|void|float|double|char|long)\s+\w+\s*\([^;{}]*\)\s*\{)',value)):return
    if not value or len(value.encode())>MAX_ARTIFACT:return
    value=transport_redactor.text(value)
    artifact=LastGeneratedArtifact('code',lang,value,agent.task_id,state.continuity_turn,time.monotonic());state.last_generated_artifact=artifact
    agent.emit('GENERATED_ARTIFACT',artifact.metadata())


def attribution(resolution,fallback):
    """Only unambiguous new canonical tasks; never rewrite historical rows."""
    if not resolution or not resolution.handled or resolution.ambiguity_reason:return str(fallback),None
    slots=resolution.slots
    if resolution.intent_id==I.CREATE_FILE and slots:
        if slots.language_hint:
            return ('PYTHON_CODING' if slots.language_hint=='python' else 'GODOT_CODE' if slots.language_hint=='gdscript' else 'CODE_GENERATION'),'code_generation'
        return 'FILE_CREATION',None
    if resolution.intent_id==I.CODE_GENERATION_RESPONSE:return 'CODE_GENERATION','code_generation'
    if resolution.intent_id==I.EXPLAIN_CODE:return 'EXPLANATION','explanation'
    return str(fallback),None

