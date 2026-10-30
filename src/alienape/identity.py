"""Small stable model identity; permissions are still enforced by the controller."""
import json

IDENTITY = """Du bist AlienApe, der KI-Worker im lokalen AlienApe-Desktop-Agenten.
Sei freundlich, neugierig, knapp und praktisch; gelegentlich leicht verspielt.
Nenne dich AlienApe, nicht generischer Chatassistent. Controller-Zustand, angebotene Tools,
Write-Scope und Freigaben sind maßgeblich. Erfinde keine Fähigkeiten oder Einschränkungen.
Behaupte Aktionen nur nach Controller-Bestätigung. Benenne Unsicherheit.
"""


def identity_system(content=''):
    """One construction point for ordinary model-facing system identity."""
    return content if content.startswith(IDENTITY) else IDENTITY+content


def controller_facts(agent):
    """Public operational facts only: no endpoints, paths, credentials or raw settings."""
    rules=agent.settings.permissions
    return 'CONTROLLER_CAPABILITIES\n'+json.dumps({
        'workspace_available':agent.workspace.root.is_dir(),
        'controller_tools':sorted(agent.tools.tools),
        'write_rule':rules.get('write_file','ask'),
        'directory_rule':rules.get('make_directory','ask'),
        'permission_handler':agent.permission is not None,
        'scope':'Current task scope only; global tools remain available for other authorized tasks. Never infer global inability from this task scope. Rules are not action confirmation.',
    },ensure_ascii=False)
