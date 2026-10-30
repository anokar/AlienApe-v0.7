"""Canonical intent adapter to existing controller primitives; no language matching."""
from dataclasses import replace
from .intent_resolution import IntentID as I
from .contracts import WriteScope,ResponseContract,ResponseMode
from .completion import CompletionRejected,FailureType
from .i18n import localize

def apply_resolution(agent,resolution):
    if not resolution.handled:return
    paths=list(resolution.referenced_paths);outputs=list(resolution.output_paths or paths)
    for path in paths:agent.workspace.path(path)
    mutation=resolution.intent_id in {I.CREATE_FILE,I.MODIFY_FILE,I.CREATE_DIRECTORY}
    if mutation:
        agent.write_scope=WriteScope(False,outputs,True,resolution.intent_id==I.CREATE_FILE,resolution.intent_id==I.CREATE_DIRECTORY,
            agent.write_scope.protect_tests,False,outputs if resolution.intent_id==I.CREATE_FILE else [])
        if agent.write_scope.protect_tests:
            from .intents import is_test_path
            agent.write_scope.allowed_paths=[p for p in outputs if not is_test_path(p)]
    else:agent.write_scope=WriteScope()
    agent.required_input_paths=[p for p in paths if p not in outputs or resolution.intent_id not in {I.CREATE_FILE,I.CREATE_DIRECTORY}]
    if resolution.intent_id==I.MODIFY_FILE:
        for p in outputs:
            if not agent.workspace.path(p).is_file():raise CompletionRejected(FailureType.INFORMATION_MISSING,'Bestehende Änderungsdatei fehlt: '+p)
    if not agent.response_contract.expects_json:
        mode=ResponseMode.FILE_PROPOSAL if mutation and resolution.intent_id!=I.CREATE_DIRECTORY else ResponseMode.ANALYSIS_RESULT if resolution.intent_id in {I.ANALYZE_FILE,I.EXPLAIN_CODE} and paths else ResponseMode.PLAIN_TEXT
        if resolution.intent_id==I.CREATE_FILE and resolution.slots and resolution.slots.content_mode=='GENERATE' and len(outputs)==1 and resolution.slots.complete:
            mode=ResponseMode.FILE_CONTENT
        agent.response_contract=ResponseContract(mode,expects_file_proposal=mode==ResponseMode.FILE_PROPOSAL,expects_plain_text=mode!=ResponseMode.FILE_PROPOSAL)
        if mode==ResponseMode.FILE_CONTENT:agent.emit('FILE_CONTENT_CONTRACT',{'contract':'FILE_CONTENT','target_source':resolution.slots.slot_sources.get('target_path'),'target_count':len(outputs)})
    agent.workspace_operation='analysis' if resolution.intent_id in {I.VERIFY_PROJECT,I.ANALYZE_DIRECTORY} else None
    if paths:
        # Compatibility adapter for the existing classifier, not a new router policy.
        agent.scope_task=resolution.intent_id.value+' '+', '.join('"'+p+'"' for p in paths)
    elif resolution.intent_id in {I.EXPLAIN_CODE,I.CODE_GENERATION_RESPONSE,I.GENERAL_CHAT}:
        # Knowledge without a workspace is GENERAL, not a file/code mutation task.
        agent.scope_task='General explanation'  # Intent/slots remain authoritative for attribution and eligibility.

async def execute_controller_intent(agent):
    r=agent.intent_resolution
    if not r or not r.handled:return False,None
    if r.intent_id==I.CAPABILITY_QUERY:
        from .direct_responses import direct_response
        return True,direct_response(agent,agent.current_task)
    if r.intent_id==I.CREATE_DIRECTORY:
        if 'make_directory' not in agent.tools.tools:
            return True,agent.finish(agent.current_task,localize('DIRECTORY_UNAVAILABLE',agent.settings.ui.language))
        for p in r.output_paths or r.referenced_paths:await agent.execute_tool('make_directory',{'path':p},origin='controller')
        return True,agent.finish(agent.current_task,localize('DIRECTORY_CREATED',agent.settings.ui.language,paths=', '.join(r.output_paths or r.referenced_paths)))
    if r.intent_id==I.CREATE_FILE and r.literal_content is not None:
        proposals=[{'path':p,'content':r.literal_content} for p in r.output_paths or r.referenced_paths]
        for proposal in proposals:
            agent.tools.validate('write_file',proposal)
            if not agent.write_scope.permits(proposal['path'],agent.workspace):raise PermissionError('Literal-Dateiziel außerhalb des Schreibbereichs.')
        for proposal in proposals:await agent.execute_tool('write_file',proposal,origin='controller')
        return True,agent.finish(agent.current_task,localize('FILE_CREATED',agent.settings.ui.language,paths=', '.join(p['path'] for p in proposals)))
    if r.intent_id==I.VERIFY_PROJECT:
        return True,await agent.project_check()
    if r.intent_id==I.FIND_FILE:
        return True,agent.finish(agent.current_task,localize('FILES_FOUND',agent.settings.ui.language,paths=', '.join(r.referenced_paths)))
    if r.intent_id==I.READ_FILE:
        answers=[]
        for p in r.referenced_paths:answers.append(await agent.read_file_direct(agent.current_task,p))
        return True,'\n\n'.join(answers)
    if r.intent_id==I.VERIFY_FILE:
        from .task_state import findings_from_verification
        files=[]
        for p in r.referenced_paths:
            await agent.execute_tool('read_file',{'path':p},origin='controller');files.append(p)
        result=await agent.verification.inspect(files)
        agent.project_verifications=[result];agent.project_findings=findings_from_verification(result,agent.workspace,agent.task_id)
        return True,agent.finish(agent.current_task,'Dateiprüfung: '+result.verification_status+'\n'+result.message+'\nUntersucht: '+', '.join(files)+'\nBegrenzter Prüfumfang; keine allgemeine Bugfreiheitsgarantie.')
    return False,None

async def provide_explicit_inputs(agent):
    r=agent.intent_resolution
    if not r or not r.handled or not agent.required_input_paths:return []
    files=[]
    for path in agent.required_input_paths:
        result=await agent.execute_tool('read_file',{'path':path},origin='controller')
        files.append({'path':path,'content':result['content']})
    return [{'role':'system','content':'WORKSPACE_INPUTS\n'+__import__('json').dumps({'untrusted_data':True,'files':files},ensure_ascii=False)}]

def find_explicit_targets(agent,resolution):
    if I.FIND_FILE not in resolution.operations and resolution.intent_id!=I.FIND_FILE:return resolution
    from .discovery import IGNORED
    from .security import SensitiveFilePolicy
    queue=['.'];found={p:[] for p in resolution.referenced_paths};visited=0
    while queue and visited<4:
        directory=queue.pop(0);visited+=1
        listing=agent.tools.execute('list_files',{'path':directory,'limit':100})
        for entry in listing['entries']:
            p=entry['path'].replace('\\','/')
            if SensitiveFilePolicy.sensitive(p) or any(part.casefold() in IGNORED for part in p.split('/')):continue
            if entry['directory']:
                if p.count('/')<2:queue.append(p)
            else:
                for target in found:
                    if p.casefold()==target.replace('\\','/').casefold() or p.split('/')[-1].casefold()==target.casefold():found[target].append(p)
    if any(len(matches)!=1 for matches in found.values()):return replace(resolution,ambiguity_reason='Dateisuche ist nicht eindeutig oder ohne Treffer; bitte den genauen relativen Pfad nennen.')
    paths=tuple(dict.fromkeys(matches[0] for matches in found.values()))
    return replace(resolution,referenced_paths=paths,output_paths=paths if resolution.mutation_requested else ())
