"""Deterministic, bounded discovery of the selected workspace. No inference or writes."""
from dataclasses import dataclass,field,asdict
from copy import deepcopy
from pathlib import PurePosixPath
import asyncio,re
from .completion import CompletionRejected,FailureType as F
from .security import SensitiveFilePolicy

IGNORED={'.git','.hg','.svn','node_modules','.venv','venv','__pycache__','.cache','cache','caches',
         'build','dist','.godot','.import','imports','import_cache','target','bin','obj','.next','.nuxt','coverage','.alienape-verification'}
TEXT_EXTENSIONS={'.gd','.godot','.tscn','.tres','.cfg','.ini','.json','.toml','.yaml','.yml','.py','.md','.txt',
                 '.js','.ts','.jsx','.tsx','.cs','.csproj','.sln','.rs','.lua','.go','.c','.h','.cpp'}
PRIORITY_NAMES={'project.godot':0,'pyproject.toml':1,'package.json':1,'requirements.txt':1,'cargo.toml':1}


def directory_candidate(task):
    text=task.strip()
    pattern=r'(?i)^\s*(?:(?:bitte|please)\s+)?(?:analys\w*|analy[sz]\w*|prüf\w*|pruef\w*|check|inspect)\s+(?:(?:project\s+folder|projektordner|ordner|folder|directory)\s+)?([\w./\\-]+/)(?:\s*[.!?])?\s*$'
    match=re.fullmatch(pattern,text)
    if not match:
        match=re.fullmatch(r'(?i)^\s*(?:check|inspect|analys\w*|prüf\w*)\s+(?:project\s+folder|projektordner|ordner|folder|directory)\s+([\w./\\-]+)\s*[.!?]?\s*$',text)
    return match.group(1) if match else None

def workspace_intent(task):
    from .routing import referenced_paths
    if directory_candidate(task):return 'analysis'
    if referenced_paths(task):return None
    text=' '.join(task.casefold().strip().rstrip('.!?').split())
    if re.match(r'merke\s+dir\b|remember\b',text):return None
    listing=(r'(?:please )?(?:list|show)(?: me)?(?: the)? (?:files|project files)(?: and (?:folders|directories))?'
             r'(?: (?:in|of)(?: the)?(?: current)? (?:project(?: folder)?|workspace|folder|directory|repository|repo))?')
    german=r'(?:bitte )?(?:liste|zeige|zeig)(?: mir)?(?: die)? (?:dateien|projektdateien)(?: (?:im|des|aus dem)(?: aktuellen)? (?:projekt(?:ordner)?|workspace|ordner|verzeichnis))?(?: auf| an)?'
    if re.fullmatch(listing,text) or re.fullmatch(german,text) or text=='dateien auflisten':return 'listing'
    from .intents import information_need
    if information_need(task):return 'analysis'
    target=re.search(r'\b(project|workspace|repository|repo|directory|folder|projekt\w*|arbeitsbereich|verzeichnis|ordner)\b',text)
    inspect=re.search(r'\b(analys\w*|analyz\w*|inspect\w*|examine|review|untersuch\w*|prüf\w*|ueberpruef\w*)\b',text)
    if target and inspect:
        from .contracts import WriteScope
        if WriteScope.for_task(task).read_only:return 'analysis'
    return None


@dataclass(frozen=True)
class DiscoveryLimits:
    directories:int=4
    depth:int=2
    entries:int=200
    entries_per_directory:int=80
    file_reads:int=5
    file_bytes:int=8192
    total_bytes:int=12000


@dataclass
class DiscoveryResult:
    files:list=field(default_factory=list)
    listings:dict=field(default_factory=dict)
    discovered:list=field(default_factory=list)
    skipped:list=field(default_factory=list)
    entries_seen:int=0
    bytes_read:int=0
    read_attempts:int=0
    listing_truncated:bool=False
    limits:dict=field(default_factory=dict)
    intent_group:str|None=None
    target_terms:list=field(default_factory=list)

    def metadata(self):
        return {'scope':'bounded_workspace','project_complete':False,'data_available':bool(self.files),
            'listed_directories':list(self.listings),'files_discovered':self.discovered[:40],
            'discovered_count':len(self.discovered),'files_read':[f['path'] for f in self.files],
            'entries_seen':self.entries_seen,'bytes_read':self.bytes_read,'read_attempts':self.read_attempts,
            'listing_truncated':self.listing_truncated,'limits':self.limits,
            'intent_group':self.intent_group,'target_terms':self.target_terms,
            'note':'Only the selected examined files support this analysis; not every project file has been inspected.'}

    def context_message(self):
        import json
        return {'role':'system','content':'WORKSPACE_DISCOVERY\n'+json.dumps({'untrusted_data':True,
            'manifest':self.metadata(),'files':self.files},ensure_ascii=False)}

    def cached_listing(self,path,workspace):
        key=workspace.path(path).relative_to(workspace.root).as_posix()
        if key not in self.listings:raise ValueError('Ordner außerhalb des begrenzten Discovery-Snapshots')
        return deepcopy(self.listings[key])


def file_priority(path):
    p=PurePosixPath(path);name=p.name.casefold()
    score=-1 if name.startswith('readme') else PRIORITY_NAMES.get(name,2 if p.suffix in {'.sln','.csproj'} else 3 if p.suffix in {'.cfg','.ini','.toml','.json','.yaml','.yml'} else 4)
    return score,len(p.parts),path.casefold()


async def discover(workspace,execute,check_stop,context_budget,limits=None,task='',start='.'):
    limits=limits or DiscoveryLimits()
    result=DiscoveryResult(limits=asdict(limits))
    from .intents import information_need,target_terms,is_test_path,SOURCE_SUFFIXES
    result.intent_group=information_need(task);result.target_terms=target_terms(task,result.intent_group)
    byte_budget=min(limits.total_bytes,max(256,context_budget//3))
    result.limits['effective_content_bytes']=byte_budget
    queue=[(workspace.path(start).relative_to(workspace.root).as_posix(),0)];candidates=[]
    while queue and len(result.listings)<limits.directories and result.entries_seen<limits.entries:
        check_stop();relative,depth=queue.pop(0)
        limit=min(limits.entries_per_directory,limits.entries-result.entries_seen)
        listing=await execute('list_files',{'path':relative,'limit':limit})
        check_stop()
        result.listings[relative]=listing
        entries=listing.get('entries',[])[:limit]
        result.entries_seen+=listing.get('scanned_entries',len(entries));result.listing_truncated |= bool(listing.get('truncated'))
        directories=[]
        for entry in entries:
            check_stop();path=entry['path'].replace('\\','/');parts=PurePosixPath(path).parts
            if SensitiveFilePolicy.sensitive(path) or any(part.casefold() in IGNORED for part in parts):
                result.skipped.append({'path':path,'reason':'ignored_directory'});continue
            safe=workspace.path(path)
            if entry['directory']:
                if depth<limits.depth:directories.append((path,depth+1))
                continue
            result.discovered.append(path)
            name=safe.name.casefold()
            if safe.suffix.casefold() not in TEXT_EXTENSIONS and not (name.startswith('readme') and safe.suffix.casefold() in {'','.md','.txt'}):
                result.skipped.append({'path':path,'reason':'unsupported_or_binary'});continue
            candidates.append(path)
        directories.sort(key=lambda item:(PurePosixPath(item[0]).name.casefold() not in {'src','scripts','scenes','source'},item[0].casefold()))
        queue.extend(directories)
        await asyncio.sleep(0)
    result.listing_truncated |= bool(queue)
    reserved=0
    code_pair=result.intent_group in {'DEBUG_EXISTING_CODE','REFACTOR_EXISTING_CODE','MODIFY_EXISTING_CODE'}
    def priority(path):
        name=PurePosixPath(path).name.casefold()
        targeted=any(term in name for term in result.target_terms)
        return (-2 if targeted else -1 if code_pair and (is_test_path(path) or PurePosixPath(path).suffix in SOURCE_SUFFIXES) else 0,
                0 if code_pair and is_test_path(path) else 1,*file_priority(path))
    for relative in sorted(candidates,key=priority):
        check_stop()
        if result.read_attempts>=limits.file_reads:break
        path=workspace.path(relative);length=path.stat().st_size;charge=max(1,length)
        if charge>limits.file_bytes or charge>byte_budget-reserved:
            result.skipped.append({'path':relative,'reason':'byte_limit'});continue
        result.read_attempts+=1;reserved+=charge
        try:data=await execute('read_file',{'path':relative,'max_bytes':charge})
        except (UnicodeError,ValueError) as exc:
            # A binary/malformed text candidate is not a model capability problem.
            result.skipped.append({'path':relative,'reason':type(exc).__name__});continue
        check_stop()
        content=data['content'];result.bytes_read+=len(content.encode('utf-8'))
        result.files.append({'path':relative,'content':content})
        await asyncio.sleep(0)
    if not result.files:raise CompletionRejected(F.INFORMATION_MISSING,'Workspace Discovery fand keine lesbaren relevanten Textdateien innerhalb der Limits.')
    if result.target_terms and not any(any(t in f['path'].casefold() or t in f['content'].casefold() for t in result.target_terms) for f in result.files):
        raise CompletionRejected(F.INFORMATION_MISSING,'Keine passende Quelle für den angefragten Teilbereich innerhalb der Discovery-Limits gefunden.')
    if code_pair:
        has_source=any(PurePosixPath(f['path']).suffix in SOURCE_SUFFIXES and not is_test_path(f['path']) for f in result.files)
        needs_tests=result.intent_group=='DEBUG_EXISTING_CODE' and bool(re.search(r'\btests?\b',task,re.I)) or any(is_test_path(p) for p in candidates)
        if not has_source or needs_tests and not any(is_test_path(f['path']) for f in result.files):
            raise CompletionRejected(F.INFORMATION_MISSING,'Benötigte Source-/Testdateien konnten nicht vollständig innerhalb der Limits beschafft werden.')
    return result
