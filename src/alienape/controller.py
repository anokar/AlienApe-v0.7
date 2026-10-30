from dataclasses import dataclass, asdict
import time
import re
from .completion import FailureType


@dataclass
class ResourceCounters:
    model_calls: int = 0
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    context_size: int = 0
    file_reads: int = 0
    bytes_read: int = 0
    file_writes: int = 0
    memory_reads: int = 0
    memory_writes: int = 0
    tool_calls: int = 0
    cache_hits: int = 0
    elapsed_time: float = 0
    resource_retries: int = 0


class ResourceController:
    def __init__(self, model=""):
        self.model = model
        self.counters = ResourceCounters()
        self.started = time.monotonic()
        self.cache = {}
        self.per_model = {}

    def select_model(self, task):
        return self.model

    def classify_completion(self,response,validation):
        reason = response.finish_reason.casefold()
        if response.tool_calls or reason=='tool_calls':
            return FailureType.TOOL_FAILURE if not response.tool_calls else FailureType.UNKNOWN
        if validation.valid is True:
            return FailureType.SUCCESS
        if validation.failure_type in {FailureType.INFORMATION_MISSING,FailureType.PERMISSION_FAILURE,FailureType.TOOL_FAILURE,FailureType.TIMEOUT}:
            return validation.failure_type
        # Evidence must be explicit: a length stop alone is not proof of exhaustion.
        meta=response.completion
        exhausted=(type(meta.output_tokens) is int and type(meta.requested_output_limit) is int
                   and meta.requested_output_limit>0 and meta.output_tokens>=meta.requested_output_limit-1)
        empty=not (response.content or '').strip() and not getattr(response,'proposed_files',[])
        if reason=='length' and exhausted and empty and validation.valid is not False:
            return FailureType.RESOURCE_OUTPUT_LIMIT
        if reason in {'length','max_tokens','max_output_tokens','limit'} and validation.complete is False:
            return FailureType.RESOURCE_OUTPUT_LIMIT
        if reason in {'stop','end_turn'} and validation.valid is False:
            return validation.failure_type
        return FailureType.UNKNOWN

    def next_output_budget(self,current,policy,*,input_tokens=None,context_limit=None):
        if self.counters.resource_retries >= policy.max_retries:
            return None,FailureType.RESOURCE_OUTPUT_LIMIT,'Maximal ein Resource-Retry pro Aufgabe.'
        next_limit = min(current*policy.growth_factor,policy.max_limit)
        if next_limit <= current:
            return None,FailureType.RESOURCE_OUTPUT_LIMIT,'Konfiguriertes maximales Output-Limit erreicht.'
        if context_limit and (input_tokens is None or input_tokens+next_limit > context_limit):
            return None,FailureType.RESOURCE_CONTEXT_LIMIT,'Neues Output-Budget passt nicht in die bekannte bzw. überprüfbare Kontextreserve.'
        return next_limit,FailureType.SUCCESS,''

    def needs_memory(self, task):
        return bool(task.strip())

    def wants_memory_overview(self, task):
        """Route explicit memory questions to SQLite, never to filesystem tools."""
        text = " ".join(task.casefold().strip().split())
        text = re.sub(r"[.!?,…]+$", "", text).strip()
        text = text.replace("erinern", "erinnern")
        patterns = (
            r"was (?:hast du|haben wir) (?:dir |uns )?(?:alles )?(?:gemerkt|gespeichert|notiert)",
            r"(?:an was|woran) (?:müssen|sollen) wir uns erinnern",
            r"was (?:müssen|sollen) wir uns merken",
            r"(?:an was|woran) erinnerst du dich",
            r"was (?:ist|steht) (?:alles )?im (?:memory|gedächtnis|speicher)",
            r"(?:zeige|zeig|liste) (?:mir )?(?:alle |meine |unsere |die )?(?:gespeicherten )?(?:erinnerungen|memories|projektinformationen|projektregeln)(?: an| auf)?",
            r"(?:memory|erinnerungen) (?:anzeigen|auflisten)",
            r"was weisst du über mein projekt",
            r"what (?:do you remember|have you (?:remembered|memorized|saved))",
            r"show (?:me )?(?:all )?(?:project )?memor(?:y|ies)",
        )
        return any(re.fullmatch(pattern, text) for pattern in patterns)

    def needs_tools(self, task):
        # Pure acknowledgements/greetings need no file or JSON tool protocol.
        # Full match keeps combined requests such as "Danke, lies x" on the agent path.
        text = re.sub(r"[.!?,…]+$", "", task.strip().casefold()).strip()
        return text not in {"ah cool", "cool", "super", "danke", "vielen dank", "dankeschön",
                            "alles klar", "ok", "okay", "hallo", "hi", "hey", "thanks", "thank you"}

    def snapshot(self):
        self.counters.elapsed_time = round(time.monotonic() - self.started, 3)
        result = asdict(self.counters)
        if self.per_model:
            result["per_model"] = {key: asdict(value) for key, value in self.per_model.items()}
        return result

    def cached_read(self, path):
        stat = path.stat()
        item = self.cache.get(str(path))
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)
        if item and item[0] == stamp:
            self.counters.cache_hits += 1
            return item[1]
        return None

    def remember_read(self, path, result):
        stat = path.stat()
        self.cache[str(path)] = ((stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns), result)
