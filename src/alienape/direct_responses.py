"""L0: four tiny social replies and a controller-grounded capability report."""
from .intent_resolution import IntentID as I
from .i18n import effective_locale, localize

RESPONSES={
    I.GREETING:'Hey :P AlienApe ist bereit. 👽🐒',
    I.THANKS:'Gern. 👽🐒',
    I.GOODBYE:'Bis bald. AlienApe ist beim nächsten Auftrag wieder bereit.',
    I.IDENTITY_QUERY:'Ich bin AlienApe, dein lokaler KI-Agent und Controller für Modelle, Tools und Projektaufgaben.',
}


def capability_answer(agent):
    names=set(agent.tools.tools);workspace=agent.workspace.root.is_dir()
    language=agent.settings.ui.language
    if effective_locale(language)!='de':
        t=lambda key:localize(key,language)
        lines=['Workspace: '+t('AVAILABLE' if workspace else 'NOT_AVAILABLE')+'.']
        for name,key in [('read_file','READ_FILES'),('list_files','LIST_FOLDERS'),('search_files','SEARCH_FILES')]:
            lines.append(t(key)+': '+t('AVAILABLE' if workspace and name in names else 'NOT_AVAILABLE')+'.')
        for name,key in [('write_file','WRITE_FILES'),('make_directory','CREATE_DIRECTORY')]:
            rule=agent.settings.permissions.get(name,'ask')
            available=workspace and name in names and rule!='block' and agent.permission is not None
            lines.append(t(key)+': '+t('AVAILABLE' if available else 'NOT_AVAILABLE')+' ('+rule+').')
        return '\n'.join(lines+[t('CAPABILITY_SCOPE'),t('CAPABILITY_LIMIT')])
    lines=['Workspace: verfügbar.' if workspace else 'Workspace: nicht verfügbar; bitte ein Projekt öffnen.']
    for name,label in [('read_file','Dateien lesen'),('list_files','Ordner auflisten'),('search_files','Text suchen')]:
        lines.append(label+(': verfügbar.' if workspace and name in names else ': nicht verfügbar.'))
    for name,label in [('write_file','Dateien erstellen/ändern'),('make_directory','Ordner erstellen')]:
        rule=agent.settings.permissions.get(name,'ask')
        if not workspace or name not in names:status='nicht verfügbar'
        elif rule=='block':status='block (gesperrt)'
        elif agent.permission is None:status=rule+' (zurzeit nicht verfügbar: kein Freigabehandler verbunden)'
        else:status=rule+' (nur im erlaubten Dateibereich; Freigaben gelten für die konkrete Aktion)'
        lines.append(label+': '+status+'.')
    lines.append('Globale Fähigkeit: Dateien und Ordner können mit den genannten Werkzeugen erstellt werden; der aktuelle Auftrag kann schreibgeschützt sein.')
    lines.append('Programmiersprachen: Die tatsächliche Codefähigkeit hängt vom ausgewählten Modell ab; ohne passende Modell-Evidenz ist keine verlässliche Sprachliste bekannt. Sprachsyntax bedeutet keine Shell-Ausführung.')
    lines.extend(['Vorhandene Python-/Godot-Prüfer nur, sofern verfügbar und freigegeben.',
                  'Kein Löschtool, keine allgemeine Shell und kein freier Netzwerkzugriff.',
                  'Eine Berechtigungsaussage im Chat ändert keine Freigabe und führt keine Aktion aus.'])
    profiles=getattr(getattr(agent,'registry',None),'profiles',{})
    modes=sorted({p.tool_mode for p in profiles.values() if p.tool_mode in {'none','native','json_fallback'}})
    if modes:lines.append('Konfigurierte Modellmodi: '+', '.join(modes)+'.')
    return '\n'.join(lines)


def direct_response(agent,task):
    r=agent.intent_resolution
    if not r or not r.handled:return None
    if r.intent_id==I.MODEL_STATUS_QUERY:
        manual=getattr(agent,'manual_model','') or (agent.settings.routing.manual_model if agent.settings.routing.mode=='manual' else '')
        profiles=getattr(getattr(agent,'registry',None),'profiles',{})
        selected=profiles.get(manual)
        language=agent.settings.ui.language
        mode=localize('MANUAL',language) if manual else 'AUTO'
        worker=agent.session.task_state.last_worker_model or localize('NO_WORKER',language)
        semantic=agent.settings.semantic_resolver.model if agent.settings.semantic_resolver.enabled else localize('DISABLED',language)
        answer=localize('MODEL_STATUS',language,mode=mode,selected=selected.model_name if selected else localize('MODEL_NOT_SELECTED',language),worker=worker,semantic=semantic or localize('NOT_CONFIGURED',language))
    elif r.intent_id==I.DELETE_REQUEST_UNSUPPORTED:
        answer=localize('DELETE_REQUEST_UNSUPPORTED',agent.settings.ui.language)
    elif r.intent_id==I.CREATE_PROJECT:
        answer='Eine Godot-Projektvorlage aus mehreren Dateien automatisch anzulegen ist hier noch nicht implementiert. Es wurde nichts erstellt. Nenne konkrete Dateinamen und Inhalte für einzelne, freizugebende Dateiaktionen.'
    elif r.intent_id==I.REASON_QUERY:
        previous=getattr(agent,'previous_failure_context',{})
        answer=('Bei „'+previous['task']+'“: '+previous['reason']) if previous else 'Auf welche Aufgabe oder Ablehnung beziehst du dich? Bitte kurz konkretisieren.'
    else:answer=capability_answer(agent) if r.intent_id==I.CAPABILITY_QUERY else localize(r.intent_id.value,agent.settings.ui.language) if r.intent_id in RESPONSES else None
    if answer is None:return None
    agent.check_stop()
    agent.emit('DIRECT_RESPONSE',{'source':'CONTROLLER' if r.intent_id in {I.DELETE_REQUEST_UNSUPPORTED,I.MODEL_STATUS_QUERY} else 'DIRECT_RESPONSE','intent_id':r.intent_id.value,
        'model_calls':0,'model_latency':None,'status':'Direkte Controller-Antwort · kein Modellaufruf'})
    # Preserve the recent file/finding reference; a greeting is not a new file task.
    # Do not attach an analysis footer cached from an earlier task on a reused Agent.
    agent.session.history.extend([{'role':'user','content':task},{'role':'assistant','content':answer}])
    agent.session.history=agent.session.history[-12:]
    return answer
