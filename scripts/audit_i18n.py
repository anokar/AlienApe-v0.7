"""Audit catalog integrity and flag likely user-visible untranslated literals."""
from pathlib import Path
import ast,json,re,string

ROOT=Path(__file__).resolve().parents[1]
RESOURCE=ROOT/'src/alienape/i18n'

def read_catalog(path):
    duplicates=[]
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:duplicates.append(key)
            result[key]=value
        return result
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pairs),duplicates

def placeholders(value):
    fields=[]
    for _,name,fmt,conversion in string.Formatter().parse(value):
        if name is not None:
            if not name.isidentifier() or fmt or conversion:raise ValueError('Unsupported format placeholder')
            fields.append(name)
    return sorted(fields)

def audit():
    data={};duplicate={};invalid={}
    for language in ('de','en','fr'):
        data[language],duplicate[language]=read_catalog(RESOURCE/(language+'.json'))
        for key,value in data[language].items():
            try:
                if not isinstance(value,str) or not value.strip():raise ValueError('Empty/non-string translation')
                placeholders(value)
            except (TypeError,ValueError) as exc:invalid[language+':'+key]=str(exc)
    keys=set(data['de']);missing={language:sorted(keys-set(catalog)) for language,catalog in data.items()};extra={language:sorted(set(catalog)-keys) for language,catalog in data.items()}
    mismatch={}
    for key in sorted(keys):
        try:
            shapes={language:placeholders(catalog[key]) for language,catalog in data.items() if key in catalog}
            if len({tuple(x) for x in shapes.values()})>1:mismatch[key]=shapes
        except (ValueError,TypeError):pass
    # Conservative review list, not an assertion that every candidate is user-visible.
    candidates=[];debug=[]
    pattern=re.compile(r'(?i)\b(?:datei(?:en)?|ordner|modell(?:e)?|einstellungen|freigabe|prüfung|verifikation|speichern|gespeichert|nicht verfügbar|fehler|abbrechen|inhalt|projekt|antwort)\b')
    for path in sorted((ROOT/'src/alienape').glob('*.py')):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and len(node.value)>8 and pattern.search(node.value):
                entry={'file':path.relative_to(ROOT).as_posix(),'line':node.lineno,'text':node.value[:200]}
                if path.name in {'semantic_intent.py','training.py','benchmark.py'}:debug.append(entry)
                else:candidates.append(entry)
    good=not any(duplicate.values()) and not any(missing.values()) and not any(extra.values()) and not mismatch and not invalid
    return {'catalog_integrity_pass':good,'required_key_count':len(keys),'missing':missing,'extra':extra,'duplicates':duplicate,'placeholder_mismatch':mismatch,'invalid':invalid,'unknown_key_fallback':'Message unavailable.','user_visible_review_candidates':candidates,'developer_debug_review_candidates':debug,'coverage_complete':False,'coverage_note':'Conservative source-literal review required; dynamic strings and events may also be user-visible.'}

if __name__=='__main__':
    result=audit();(ROOT/'I18N_COVERAGE_V07.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('catalog integrity:',result['catalog_integrity_pass'],'review candidates:',len(result['user_visible_review_candidates']))
