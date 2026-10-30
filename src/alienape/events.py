from datetime import datetime, timezone
from pathlib import Path
import json
import threading
import uuid
from .security import redactor


LABELS = {
    'DIRECT_RESPONSE':'Direkte Antwort ohne Modell',
    'MODEL_COMPLETION':'Modellabschluss und Ressourcen',
    'VALIDATION_RESULT':'Ausgabe geprüft',
    'FAILURE_CLASSIFIED':'Technische Ergebnisursache',
    'RESOURCE_OUTPUT_LIMIT':'Output-Budget ausgeschöpft',
    'RESOURCE_RETRY_STARTED':'Gleiches Modell mit größerem Output-Budget',
    'RESOURCE_RETRY_SUCCESS':'Resource-Retry erfolgreich',
    'RESOURCE_RETRY_FAILED':'Resource-Retry fehlgeschlagen',
    'OUTPUT_BUDGET_NEAR_LIMIT':'Vollständige Antwort nahe Output-Limit',
    'EXPERIENCE_RECORDED': 'Compute als Modellerfahrung gespeichert',
    'EXPERIENCE_AGGREGATED': 'Modellstatistik aktualisiert',
    'MODEL_SELECTED_FROM_EXPERIENCE': 'Modell anhand Erfahrung gewählt',
    'MODEL_SKIPPED_FROM_EXPERIENCE': 'Modell wegen gemessener Qualität übersprungen',
    'BENCHMARK_PROGRESS': 'Modelltest',
    "CONTROLLER_ACTION": "Direkte Dateiaktion ohne Modell",
    "TASK_CLASSIFIED": "Aufgabentyp erkannt", "MODEL_ROUTED": "Modell ausgewählt",
    "MODEL_ESCALATED": "Stärkeres Modell angefordert", "TOOL_DELEGATED": "Controller verwendet Werkzeug",
    "TASK_PACKET": "Relevante Daten übergeben", "MODEL_STATS": "Modellverbrauch aktualisiert",
    "MODEL_FORMAT_NORMALIZED": "Strukturierter Vorschlag geprüft",
    "USER_MESSAGE": "Nachricht gesendet", "MODEL_CALL": "Modell gefragt",
    "MODEL_RESPONSE": "Modellantwort empfangen", "TOOL_REQUEST": "Werkzeug angefordert",
    "TOOL_APPROVED": "Werkzeug freigegeben", "TOOL_DENIED": "Werkzeug abgelehnt",
    "TOOL_RESULT": "Werkzeugergebnis erhalten", "FILE_READ": "Datei gelesen",
    "FILE_WRITE": "Datei geschrieben", "MEMORY_STORE": "Memory gespeichert",
    "MEMORY_RETRIEVE": "Memory gesucht", "MEMORY_SUPERSEDE": "Frühere Entscheidung ersetzt",
    "AGENT_STEP": "Agent-Schritt", "ERROR": "Fehler", "STOP": "Gestoppt",
}


class EventLog:
    def __init__(self, directory, project_id="", session_id=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.project_id, self.session_id = project_id, session_id or uuid.uuid4().hex
        self.lock = threading.Lock()

    def write(self, kind, details=None, step=0, resources=None, duration=None, diagnostic_trace_id=None):
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "session_id": self.session_id,
                 "project_id": self.project_id, "step": step, "event_type": kind,
                 "details": details or {}, "duration": duration if duration is not None else (details or {}).get("duration", 0.0),
                 "resources": resources or {}}
        if diagnostic_trace_id:event['diagnostic_trace_id']=diagnostic_trace_id
        event = redactor.clean(event)
        # Bounded logging, no reasoning fields are ever passed in from a provider.
        encoded = json.dumps(event["details"], ensure_ascii=False, default=str)
        if len(encoded) > 16000:
            event["details"] = {"preview": encoded[:16000], "truncated": True}
        line = json.dumps(event, ensure_ascii=False, default=str)
        readable = f"{event['timestamp']} {LABELS.get(kind, kind)} | {json.dumps(event['details'], ensure_ascii=False)}\n"
        with self.lock:
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
            with (self.directory / "activity.log").open("a", encoding="utf-8") as stream:
                stream.write(readable)
        return event
