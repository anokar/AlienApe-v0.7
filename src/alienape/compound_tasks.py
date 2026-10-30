"""Conservative, bounded patterns for independently executed linear requests."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class CompoundStep:
    task:str
    independent:bool=False
    expected_path:str=''
    expected_kind:str=''

@dataclass(frozen=True)
class CompoundPlan:
    steps:tuple=()
    clarification:str=''

def memory_payload(task):
    # Only strip a complete, negative instruction about showing existing secrets;
    # never strip a clause that might itself be a value to be stored.
    match=re.fullmatch(r'(?is)\s*(?:merk(?:e)?\s+dir|remember)\s*:?[ ]*(.+?)\s+aber\s+(?:wenn\s+da\s+irgendein\s+)?(?:vorhandene[nr]?\s+|in\s+deiner\s+memory\s+)?(?:api[ -]?keys?|zugangsdaten)(?:\s+in\s+deiner\s+memory)?\s+(?:ist\s+)?(?:zeig(?:e)?\s+(?:den|die|das)\s+nicht|nicht\s+zeigen|nicht\s+ausgeben)\s*[.!?]?\s*',task)
    if not match:
        match=re.fullmatch(r'(?is)\s*(?:merk(?:e)?\s+dir|remember)\s*:?[ ]*(.+?)\s+aber\s+(?:zeig(?:e)?|show)\s+vorhandene\s+(?:api[ -]?keys?|zugangsdaten)\s+nicht\s*[.!?]?\s*',task)
    if not match:
        match=re.fullmatch(r'(?is)\s*merk(?:e)?\s+dir\s*:?[ ]*(.+?)\s+aber\s+zeig(?:e)?\s+keine\s+api[ -]?keys?\s+aus\s+deiner\s+memory\s*[.!?]?\s*',task)
    if match:return 'merke dir: '+match.group(1).strip()
    explicit=re.fullmatch(r'(?is)\s*merk(?:e)?\s+dir\s*(?::\s*|\s+)(?!\[)(\S[\s\S]*)',task)
    if explicit:
        payload=explicit.group(1).strip()
        project=re.fullmatch(r'(?is)für\s+dieses\s+projekt\s*:\s*(\S[\s\S]*)',payload)
        return 'merke dir: '+(project.group(1).strip() if project else payload)
    english=re.fullmatch(r'(?is)\s*remember\s+(?:that\s+)?(\S[\s\S]*)',task)
    if english:return 'merke dir: '+english.group(1).strip()
    project=re.fullmatch(r'(?is)\s*merk(?:e)?\s+dir\s+für\s+dieses\s+projekt\s*:\s*(\S[\s\S]*)',task)
    return 'merke dir: '+project.group(1).strip() if project else task

def unquoted(text):
    chars=list(text);quote=None;escaped=False
    for i,ch in enumerate(text):
        if quote:
            chars[i]=' '
            if escaped:escaped=False
            elif ch=='\\':escaped=True
            elif ch==quote:quote=None
        elif ch in ('"',"'",'`','„','“'):
            quote={'„':'“','“':'”'}.get(ch,ch);chars[i]=' '
    return ''.join(chars)

def split_compound(task):
    clean=unquoted(task)
    # This single construction establishes an immediate lexical directory binding.
    m=re.fullmatch(r'(?is)\s*(?:(?:mach|mache|erstelle|erstel|create|make)\s+)(?:wenigstens\s+)?(?:(?:ein(?:en?)?|a)\s+)?(?:ordner|folder|directory)\s+([\w.-]+)\s+und\s+(?:eine?\s+)?(?:leere?\s+|empty\s+)([\w.-]+\.[\w-]+)\s+(?:darin|inside(?:\s+it)?)\s*[.!?]?\s*',clean)
    if m:
        directory,name=m.group(1),m.group(2)
        return CompoundPlan((CompoundStep('erstelle einen Ordner '+directory,expected_path=directory,expected_kind='directory'),CompoundStep('erstelle '+directory+'/'+name+' leer',expected_path=directory+'/'+name,expected_kind='file')))
    capability=re.fullmatch(r'(?is)(.+?)\s+und\s+(?:sag|sage|erzähl|erzaehl)\s+danach\s+was\s+du\s+mit\s+dateien\s+kannst\s*[.!?]?\s*',clean)
    if capability:
        boundary=clean.rfind(' und ',0,capability.end())
        return CompoundPlan((CompoundStep(task[:boundary].strip()),CompoundStep('Was kannst du wirklich mit Dateien machen?',True)))
    # Explicit generation followed by saving the immediately preceding artifact.
    save=re.fullmatch(r'(?is)(.+?)\s+und\s+(?:speicher\w*|save)\s+(?:sie|es|das|it)\s+(?:danach|dann|then)\s+(?:als|as|in)\s+([\w.-]+\.[\w-]+)\s*[.!?]?\s*',clean)
    if save and re.search(r'(?i)\b(?:funktion|function|code)\b',save.group(1)):
        boundary=clean.rfind(' und ',0,save.end())
        return CompoundPlan((CompoundStep(task[:boundary].strip()),CompoundStep('speichere die funktion in '+save.group(2),expected_path=save.group(2),expected_kind='file')))
    status=re.fullmatch(r'(?is)(.+?)\s+und\s+welches?\s+model+l?\s+macht\s+das\s+gerade\s*[.!?]?\s*',clean)
    if status:
        boundary=clean.rfind(' und ',0,status.end())
        first=task[:boundary].strip()
        # Conditional missing-folder wording does not change the directory target.
        first=re.sub(r'(?is)\s+wenn\s+der\s+ordner\s+nicht\s+da\s+ist.*$','',first).strip(' ,')
        return CompoundPlan((CompoundStep(first),CompoundStep('Welches Modell benutze ich gerade?',True)))
    connectors=list(re.finditer(r'(?i)\b(?:und\s+danach|and\s+then|danach|dann|then)\b',clean))
    # The common create/read form can place danach at the end.
    trailing=re.fullmatch(r'(?is)(.+?)\s+und\s+(?:lies|lese|read)\s+(?:sie|es|die\s+datei|it|the\s+file)\s+danach\s*[.!?]?\s*',clean)
    if trailing:
        from .task_slots import lexical_paths
        boundary=clean.rfind(' und ',0,trailing.end())
        first=task[:boundary].strip()
        paths=lexical_paths(first)
        if len(paths)==1:return CompoundPlan((CompoundStep(first,expected_path=paths[0],expected_kind='file'),CompoundStep('Lies "'+paths[0]+'"')))
        return CompoundPlan(clarification='Bitte nenne genau eine Datei, die ich danach lesen soll.')
    if len(connectors)>2:return CompoundPlan(clarification='Bitte teile den Auftrag in höchstens drei eindeutige Schritte auf.')
    if not connectors:return None
    pieces=[];start=0
    for m in connectors:pieces.append(task[start:m.start()].strip(' ,'));start=m.end()
    pieces.append(task[start:].strip(' ,'))
    if any(not p for p in pieces):return CompoundPlan(clarification='Bitte formuliere die einzelnen Schritte eindeutig.')
    if len(pieces)==2:
        first,second=pieces;lower=unquoted(second).casefold()
        from .task_slots import lexical_paths
        paths=lexical_paths(first)
        if re.match(r'(?i)^(?:lies|lese|read)\b',second) and re.search(r'(?i)\b(?:sie|es|it|die datei|the file)\b',second) and len(paths)==1:
            return CompoundPlan((CompoundStep(first,expected_path=paths[0],expected_kind='file'),CompoundStep('Lies "'+paths[0]+'"')))
        if re.search(r'(?i)\b(?:speicher\w*|save)\b',second):
            targets=lexical_paths(second)
            if len(targets)==1 and re.search(r'(?i)\b(?:funktion|function|code)\b',first):
                return CompoundPlan((CompoundStep(first),CompoundStep('speichere das als '+targets[0],expected_path=targets[0],expected_kind='file')))
        if re.search(r'(?i)was\s+(?:du|kannst\s+du).*dateien|what.*(?:files|capabilit)',second):
            return CompoundPlan((CompoundStep(first),CompoundStep('Was kannst du wirklich mit Dateien machen?',True)))
        if re.search(r'(?i)welches?\s+model+l?|what\s+model',second):
            return CompoundPlan((CompoundStep(first),CompoundStep('Welches Modell benutze ich gerade?',True)))
    return CompoundPlan(clarification='Bitte formuliere die einzelnen Schritte mit eindeutigen Zielen. Es wurde nichts verändert.')
