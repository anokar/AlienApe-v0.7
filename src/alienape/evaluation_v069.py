"""Explicit, isolated evaluation helpers. No routing or Experience writes."""
import ast, asyncio, json, re, time
from dataclasses import asdict, replace
from pathlib import Path
from .contracts import ResponseContract, ResponseMode, normalize_response
from .completion import CompletionRejected
from .experience import ModelFingerprint
from .routing import BackendPool
from .semantic_intent import SemanticIntentResolver
from .task_packet import TaskPacket

CATALOG_VERSION = '1.0.0'
PROBE_TASKS = (
    ('py-hello', 'hello.py', 'Create hello.py containing a minimal Hello World program.'),
    ('py-chooser', 'chooser.py', 'Create chooser.py containing a small file-selection example.'),
    ('html-button', 'index.html', 'Create index.html containing a button.'),
)

def eligible_probe(profile):
    v = profile.v2()
    return profile.enabled and (v.task_capabilities.get('code_generation') == 'SUPPORTED' or v.task_capabilities.get('coding') == 'SUPPORTED' or v.protocol_capabilities.get('file_proposal') == 'SUPPORTED')

def assess_completion(text, path, *, tool_calls=False):
    """Inspect only an exact target proposal or one fenced code block; never execute it."""
    contract=ResponseContract(ResponseMode.FILE_PROPOSAL, expects_file_proposal=True)
    parsed=None
    try:
        parsed=normalize_response(text, contract, allow_proposals=True)
        valid=bool(parsed.proposed_files) and parsed.status not in ('cannot_complete','verification_failed')
    except (CompletionRejected, ValueError, TypeError): valid=False
    if tool_calls: valid=False
    proposals=parsed.proposed_files if parsed else []
    correct=valid and len(proposals)==1 and proposals[0]['path']==path
    code=''; source='NONE'
    if valid:
        if correct:
            code=proposals[0]['content']; source='FILE_PROPOSAL'
    else:
        language='python' if path.endswith('.py') else 'html' if path.endswith('.html') else None
        if language:
            # Parse complete Markdown fences first; unmatched candidate openings are ambiguous.
            fence_pattern=r'(?m)^ {0,3}```[ \t]*([^\r\n`]*)\r?\n([\s\S]*?)^ {0,3}```[ \t]*(?:\r?\n|$)'
            matches=list(re.finditer(fence_pattern,text))
            candidates=[m.group(2) for m in matches if m.group(1).strip().casefold()==language]
            remainder=re.sub(fence_pattern,'',text)
            broken=bool(re.search(r'(?mi)^ {0,3}```[ \t]*'+re.escape(language)+r'[ \t]*(?:\r?$)',remainder))
            if len(candidates)>1 or broken: source='AMBIGUOUS'
            elif len(candidates)==1: code=candidates[0];source='SINGLE_FENCE'
    extracted=bool(code.strip())
    syntax='UNKNOWN'; quality='UNKNOWN'
    if extracted and path.endswith('.py'):
        try:
            tree=ast.parse(code); syntax='PASS'
            if path=='hello.py' and any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='print' for n in ast.walk(tree)): quality='PLAUSIBLE'
            if path=='chooser.py' and any(isinstance(n,ast.Attribute) and n.attr in {'askopenfilename','getOpenFileName'} for n in ast.walk(tree)): quality='PLAUSIBLE'
        except (SyntaxError, ValueError): syntax='FAIL'
    elif extracted and path.endswith('.html') and re.search(r'<button(?:\s|>)',code,re.I):
        quality='PLAUSIBLE'
    try: json.loads(text); extra=False
    except (ValueError, TypeError): extra=bool(text.strip())
    if tool_calls: extra=True
    return {'protocol_valid':bool(valid),'valid_file_proposal':bool(valid),'correct_target_path':bool(correct),
            'unexpected_extra_text':extra,'code_extracted':extracted,'extraction_source':source,
            'syntax_status':syntax,'plausibility_status':quality,
            'syntax_valid': True if syntax=='PASS' else False if syntax=='FAIL' else None,
            'code_generation_quality':quality.lower()}

async def protocol_probe(registry, settings, model_ids, pool=None):
    if not model_ids or len(model_ids)!=len(set(model_ids)): raise ValueError('Select at least one distinct model.')
    profiles=[]
    for mid in model_ids:
        p=registry.profiles.get(mid)
        if not p or not eligible_probe(p): raise ValueError('Model is not enabled or eligible for code generation/file proposal: '+str(mid))
        profiles.append(p)
    pool=pool or BackendPool(settings.model.timeout); results=[]
    for p in profiles:
        backend=pool.get(p)
        installed=await backend.list_models()
        if p.model_name not in installed and p.model_name+':latest' not in installed: raise ValueError('Selected model is not installed: '+p.model_name)
        meta=await backend.model_identity(p.model_name)
        fp=replace(ModelFingerprint.for_profile(p,meta,settings.agent.context_budget),max_output_tokens=settings.resources.output.default_limit)
        for task_id,path,prompt in PROBE_TASKS:
            contract=ResponseContract(ResponseMode.FILE_PROPOSAL,expects_file_proposal=True)
            packet=TaskPacket(prompt,'BENCHMARK',['code_generation'],task_domain='PYTHON_CODING',response_contract=asdict(contract),resource_budget={'max_model_steps':1,'max_output_tokens':settings.resources.output.default_limit})
            started=time.monotonic(); row={'source':'protocol_probe','catalog_version':CATALOG_VERSION,'model_id':p.id,'model_fingerprint':fp.id,'task_id':task_id,'capability_v2':'code_generation','protocol_v2':'file_proposal','model_calls':1}
            try:
                with backend.output_budget(settings.resources.output.default_limit):
                    response=await backend.chat_with_mode(packet.bounded(settings.agent.context_budget).messages(),None,p.model_name,'none')
                text=response.get('content') or ''; metadata=getattr(response,'completion',None)
                row.update(assess_completion(text,path,tool_calls=bool(response.get('tool_calls'))));row['raw_completion_bytes']=len(text.encode('utf-8'))
                reason=getattr(metadata,'finish_reason','UNKNOWN'); row['output_limit_hit']=str(reason).casefold() in {'length','max_tokens','max_output_tokens','limit'}
                row['input_tokens']=getattr(metadata,'input_tokens',None);row['output_tokens']=getattr(metadata,'output_tokens',None)
                row['failure_attribution']='RESOURCE_OUTPUT_LIMIT' if row['output_limit_hit'] else 'PROTOCOL_FAILURE' if not row['valid_file_proposal'] else 'TARGET_MISMATCH' if not row['correct_target_path'] else 'NONE'
            except asyncio.CancelledError: raise
            except Exception as exc:
                row.update(raw_completion_bytes=0,protocol_valid=False,valid_file_proposal=False,correct_target_path=False,unexpected_extra_text=False,code_extracted=False,extraction_source='NONE',syntax_status='UNKNOWN',plausibility_status='UNKNOWN',syntax_valid=None,code_generation_quality='unknown',output_limit_hit=None,input_tokens=None,output_tokens=None,failure_attribution='PROVIDER_OR_HARNESS_ERROR',error_type=type(exc).__name__)
            row['latency_ms']=round((time.monotonic()-started)*1000,3);results.append(row)
    return {'source':'protocol_probe','catalog_version':CATALOG_VERSION,'results':results}

async def semantic_evaluation(settings, fixture, backend=None):
    # A copied settings object avoids modifying the configured values or activation state.
    local=replace(settings,enabled=True)
    resolver=SemanticIntentResolver(local,backend)
    results=[]
    for case in fixture['cases']:
        cached=bool(resolver.anchors and resolver.anchor_fingerprint)
        suggestion=await resolver.suggest(case['text'])
        matches=suggestion.top_matches
        results.append({'text':case['text'],'expected':case['expected'],'top_intent':matches[0]['intent_id'] if matches else None,'second_intent':matches[1]['intent_id'] if len(matches)>1 else None,
            'top_similarity':suggestion.top_similarity,'margin':suggestion.margin,'configured_threshold':suggestion.configured_confidence_threshold,'effective_threshold':suggestion.effective_confidence_threshold,
            'configured_ambiguity_margin':suggestion.configured_ambiguity_margin,'effective_ambiguity_margin':suggestion.effective_ambiguity_margin,'accepted':suggestion.accepted,'result':suggestion.match_status,'elapsed_ms':suggestion.elapsed_ms,'anchor_cache':'warm' if cached else 'cold_or_unobserved'})
    return {'catalog_version':CATALOG_VERSION,'results':results}
