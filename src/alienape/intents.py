"""Small deterministic task patterns. No inference or model-selection policy."""
import re

PATTERNS={
    'VERIFY_EXISTING_PROJECT':r'^(?:(?:bitte|please)\s+)?(?:(?:teste|prüfe|pruefe|überprüfe|ueberpruefe)\s+(?:(?:das|den|dieses|diesen)\s+)?(?:projekt|project|code|workspace)(?:\b|$)|(?:suche\s+nach\s+fehlern|finde\s+bugs)\s+(?:im|in\s+dem)\s+(?:projekt|project|workspace)|(?:test|check|verify)\s+(?:(?:the|this|existing)\s+)?(?:project|code|workspace)\b|find\s+bugs\s+in\s+(?:(?:the|this)\s+)?(?:project|workspace)|inspect\s+(?:(?:the|this)\s+)?(?:project|workspace)\s+for\s+(?:errors|bugs))',
    'FIND_IMPLEMENTATION':r'\b(?:find\s+where\s+.+?\s+(?:is|are)\s+implemented|locate\s+\w+|where\s+is\s+.+?\s+defined|finde\s+.+?implementiert|wo\s+(?:ist|sind)\s+.+?(?:definiert|implementiert))\b',
    'DEBUG_EXISTING_CODE':r'\b(?:debug\s+(?:the\s+)?(?:failing\s+tests|existing\s+code)|tests\b.{0,70}\b(?:are\s+)?failing|find\s+the\s+cause|fix\s+(?:the\s+)?implementation|(?:tests?\s+(?:schlagen\s+fehl|scheitern))|(?:fehler\s+in\s+der\s+implementierung))\b',
    'REFACTOR_EXISTING_CODE':r'\b(?:refactor\s+(?:the\s+)?(?:implementation|existing\s+code)|review\s+(?:the\s+)?implementation|(?:refaktoriere|überarbeite)\s+(?:die\s+)?implementierung)\b',
    'INSPECT_SUBSYSTEM':r'\b(?:inspect\s+(?:the\s+)?[\w -]{1,55}\s+system|(?:analyse|analyze|inspect)\s+(?:the\s+)?existing\s+code|(?:untersuche|analysiere|prüfe)\s+(?:das\s+)?[\w -]{1,45}system|(?:untersuche|analysiere)\s+(?:den\s+)?bestehenden\s+code)\b',
    'MODIFY_EXISTING_CODE':r'\b(?:add\s+support\b.{1,100}\bin\s+(?:this|the)\s+project|(?:erweitere|ergänze)\s+(?:die\s+)?bestehende\s+implementierung)\b',
}

def information_need(task):
    if re.match(r'\s*(?:merke\s+dir|remember)\b',task,re.I):return None
    text=' '.join(task.casefold().split())
    for group,pattern in PATTERNS.items():
        match=re.search(pattern,text)
        if match:
            if group=='VERIFY_EXISTING_PROJECT' and re.match(r'\.[a-z0-9_]|[/\\]',text[match.end():]):continue
            return group
    return None

def target_terms(task,group):
    if group not in {'FIND_IMPLEMENTATION','INSPECT_SUBSYSTEM'}:return []
    match=re.search(r'(?:find\s+where|locate|where\s+is|inspect|finde|untersuche|analysiere|prüfe|wo\s+(?:ist|sind))\s+(.+?)(?:[.\n!?]|$)',task,re.I)
    text=match[1] if match else ''
    stop={'the','a','an','is','are','implemented','defined','system','systems','current','existing','code','project','where','rules',
          'die','das','der','den','ein','eine','ist','sind','implementiert','definiert','regeln','bestehenden'}
    return list(dict.fromkeys(w.casefold() for w in re.findall(r'[^\W_]+',text) if len(w)>2 and w.casefold() not in stop))[:4]

def is_test_path(path):
    parts=path.replace('\\','/').casefold().split('/');name=parts[-1]
    return any(p in {'test','tests','spec','specs'} for p in parts[:-1]) or name.startswith(('test_','test.')) or name.endswith(('_test.py','.test.py','.spec.ts','.test.ts'))

SOURCE_SUFFIXES={'.py','.gd','.js','.ts','.jsx','.tsx','.cs','.rs','.go','.lua','.c','.h','.cpp'}
