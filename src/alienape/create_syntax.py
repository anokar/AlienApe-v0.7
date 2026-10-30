"""Conservative CREATE syntax boundary. Uncertain text is never a literal write."""
import re

PREFIX=r'(?:(?:bitte|please)\s+|(?:kannst|kanst) du (?:mir )?(?:bitte )?|can you (?:please )?)?'
VERB=r'(?:erstel\w*|mach\w*|schreib\w*|create|make|write)'
CONTENT=re.compile(r'\b(?:mit(?:\s+(?:(?:dem\s+)?(?:text|satz|inha(?:lt|t))))?|with(?:\s+(?:the\s+)?(?:text|content|sentence))?|containing)\s*[:.]?\s+',re.I)
QUOTES={'"':'"',"'":"'",'`':'`','„':'“','“':'”'}
RESERVED={'und','and','oder','or','mit','with','daten','data','ordner','folder','directory','project','projekt'}


def action_text(task):
    text=re.sub(r'^\s*(?:auch gut|ok|okay|gut)[.!]\s*','',task,flags=re.I)
    return re.sub(r'^\s*(?:hallo|hi|hey|hello)[,!]?\s+(?=(?:kanst|kannst|can|bitte|please|erstel|mach|create|make))','',text,flags=re.I).strip()


def content_clause(task):
    # A marker inside a quoted filename is not a content clause.
    spans=[m.span() for m in re.finditer(r'"[^"\n]*"|\x27[^\x27\n]*\x27|`[^`\n]*`|„[^“\n]*“|“[^”\n]*”',task)]
    for m in CONTENT.finditer(task):
        if not any(a<=m.start()<b for a,b in spans):return task[:m.start()].strip(),task[m.end():].strip()
    return task.strip(),None


def parenthesized_write_clause(task):
    """Explicit 'und schreib rein (...)' only; preserve the exact bounded payload."""
    quote_pattern=r'"[^"\n]*"|\x27[^\x27\n]*\x27|`[^`\n]*`|„[^“\n]*“|“[^”\n]*”'
    quoted=[m.span() for m in re.finditer(quote_pattern,task)]
    marker=None
    for candidate in re.finditer(r'\bund\s+schreib(?:e)?\s+(?:rein|hinein)\s*',task,re.I):
        if any(a<=candidate.start()<b for a,b in quoted):continue
        prefix=re.sub(quote_pattern,'',task[:candidate.start()])
        if any(c in prefix for c in ['"',"'",'`','„','“','”']):continue
        marker=candidate;break
    if not marker:return None
    header=task[:marker.start()].strip();body=task[marker.end():].strip()
    match=re.fullmatch(r'\(([^()]*)\)\s*[.!?]?\s*',body,re.S)
    if not match:return header,None,'Klammerinhalt ist unklar; bitte genau ein ausgeglichenes Klammerpaar mit Inhalt angeben.'
    return header,match.group(1),''


def literal_payload(body):
    if body is None:return None,''
    if body and body[0] in QUOTES:
        closing=QUOTES[body[0]]
        end=next((i for i in range(1,len(body)) if body[i]==closing and body[i-1]!='\\'),None)
        if end is None:return None,'Bitte den Dateiinhalt eindeutig mit passenden Anführungszeichen angeben.'
        suffix=body[end+1:]
        if re.fullmatch(r'\s*(?:(?:drin|hinein|inside)\s*)?[.!?]?\s*',suffix,re.I):return body[1:end],''
        return None,'' # Compound instruction: normal model workflow, not literal text.
    if body.startswith('('):
        match=re.fullmatch(r'\(([^()]*)\)',body,re.S)
        if not match:return None,'Klammerinhalt ist unklar; bitte genau ein ausgeglichenes Klammerpaar mit Inhalt angeben.'
        return match.group(1).strip(),''
    if any(c in body for c in ['"',"'",'`','„','“','”']):
        return None,'Bitte den Dateiinhalt eindeutig mit passenden Anführungszeichen angeben.'
    # Retain short explicit bare literals; never consume generation/compound wording.
    if (not body or '\n' in body or re.match(r'^(?:dem|der|den|einem|einen|the|a|an)\b',body,re.I)
        or re.search(r'\b(?:und|and|oder|or|drin|driin|generate|generiere|programm?|python|code|verify|prüfe|ändere)\b',body,re.I)):
        return None,''
    return body,''


def file_arguments(task,python=False):
    from .routing import referenced_paths
    header,body=content_clause(task);header=action_text(header)
    paths=tuple(referenced_paths(header))
    if not paths:paths=tuple(re.findall(r'(?<!\w)(\.env(?:\.[\w-]+)?)(?!\w)',header,re.I))
    literal,error=literal_payload(body)
    if error:return (),None,error
    # Names explicitly introduced by grammar may have no suffix.
    named=re.search(r'\b(?:namens|named|als|alls|as)\s+("[^"]+"|\x27[^\x27]+\x27|`[^`]+`|[\w./\\:-]+)\s*$',header,re.I)
    if named and not paths:
        name=named[1];paths=(name[1:-1] if name[0] in QUOTES else name,)
    # Existing generated-code request may place "as foo.py" after its description.
    if not paths and python and body is not None and (not body or body[0] not in QUOTES):
        tail=re.search(r'\b(?:als|alls|as|in)\s+([\w./\\:-]+\.py)(?:\s+erstel\w*)?[.!?]?\s*$',body,re.I)
        if tail:paths=(tail[1],);literal=None
    if len(paths)>1:return (),None,'Bitte genau einen eindeutigen neuen Dateipfad nennen.'
    location=re.search(r'\b(?:im|in|unter|inside)\s+(?:ordner|folder|directory)\s+([\w/\\.-]+)',header,re.I)
    if paths and location and location[1]!=paths[0]:
        return (),None,'Bitte den vollständigen relativen Zielpfad einschließlich des Ordners nennen.'
    if not paths:
        noun=r'(?:text ?datei?|text file|python (?:programm?|program|datei|file))'
        obj=r'(?:(?:ein\w*|a)\s+)?(?:(?:leere?|lere|empty|blank)\s+)?'+noun
        default=(re.fullmatch(PREFIX+VERB+r'\s+(?:mir\s+)?'+obj,header,re.I)
                 or re.fullmatch(PREFIX+obj+r'\s+(?:erstel\w*|machen)',header,re.I))
        if not default:return (),None,'Bitte den gewünschten neuen Dateinamen eindeutig nennen.'
        paths=('module.py' if python else 'document.txt',)
    if body is None and re.search(r'\b(?:leere?|lere|empty|blank)\b',header,re.I):literal=''
    # Non-text code is generated normally unless the user explicitly requested empty.
    if paths[0].casefold().endswith('.py') and body is not None:literal=None
    return paths,literal,''


def directory_argument(task):
    text=action_text(task)
    m=re.fullmatch(PREFIX+VERB+r'\s+(?:mir\s+)?(?:(?:ein(?:en?)?|a|the)\s+)?(?:ordner|folder|directory)\s+(?:(namens|named)\s+)?("[^"]+"|\x27[^\x27]+\x27|`[^`]+`|[\w/\\.-]+)[.!?]?\s*',text,re.I)
    if not m:return None
    name=m[2];quoted=name[0] in QUOTES
    if quoted:name=name[1:-1]
    if not quoted and name.endswith('.'):return None
    if not quoted and not m[1] and name.casefold() in RESERVED:return None
    return name
