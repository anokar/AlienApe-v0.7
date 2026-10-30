"""Observable completion data; dict compatibility keeps existing tools/backends usable."""
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class FailureType(StrEnum):
    SUCCESS = 'SUCCESS'
    RESOURCE_OUTPUT_LIMIT = 'RESOURCE_OUTPUT_LIMIT'
    RESOURCE_CONTEXT_LIMIT = 'RESOURCE_CONTEXT_LIMIT'
    TIMEOUT = 'TIMEOUT'
    FORMAT_FAILURE = 'FORMAT_FAILURE'
    VALIDATION_FAILURE = 'VALIDATION_FAILURE'
    TOOL_FAILURE = 'TOOL_FAILURE'
    PERMISSION_FAILURE = 'PERMISSION_FAILURE'
    INFORMATION_MISSING = 'INFORMATION_MISSING'
    MODEL_CAPABILITY_FAILURE = 'MODEL_CAPABILITY_FAILURE'
    UNKNOWN = 'UNKNOWN'


@dataclass
class CompletionMetadata:
    finish_reason: str = 'UNKNOWN'
    input_tokens: int | None = None
    output_tokens: int | None = None
    requested_output_limit: int | None = None
    provider_metadata: dict = field(default_factory=dict)
    elapsed_ms: float | None = None
    provider: str | None = None
    model: str | None = None
    http_status: int | None = None
    api_success: bool | None = None

    @property
    def limit_exactly_reached(self):
        return self.output_tokens == self.requested_output_limit if self.output_tokens is not None and self.requested_output_limit is not None else None

    def to_dict(self):
        return {**asdict(self), 'output_limit_exactly_reached':self.limit_exactly_reached,
                'http_success':200 <= self.http_status < 300 if self.http_status is not None else None}

    @classmethod
    def from_api(cls, result, payload, *, provider, elapsed_ms, http_status, api_success=True):
        result = result if isinstance(result,dict) else {}
        choices = result.get('choices')
        first = choices[0] if isinstance(choices,list) and choices and isinstance(choices[0],dict) else {}
        reason = first.get('finish_reason')
        source = 'choices[0].finish_reason'
        if not isinstance(reason,str) or not reason:
            reason,source = result.get('done_reason'),'done_reason'
        if not isinstance(reason,str) or not reason:
            reason,source = 'UNKNOWN',None
        usage = result.get('usage') if isinstance(result.get('usage'),dict) else {}
        def count(key,native):
            value = usage.get(key,result.get(native))
            return value if type(value) is int and value >= 0 else None
        native = {k:result[k] for k in ('done','done_reason','eval_count','prompt_eval_count',
                   'total_duration','load_duration','eval_duration','prompt_eval_duration') if k in result and isinstance(result[k],(str,int,float,bool,type(None)))}
        native['completion_reason_field'] = source
        return cls(reason,count('prompt_tokens','prompt_eval_count'),count('completion_tokens','eval_count'),
                   payload.get('max_tokens'),native,elapsed_ms,provider,payload.get('model'),http_status,api_success)


class ModelResponse(dict):
    """Only normal message fields enter history. Metadata is an attribute, never prompt data."""
    def __init__(self, message=None, *, completion=None, protocol_validator=None, protocol_validation=None):
        super().__init__({k:v for k,v in (message or {}).items() if k in {'role','content','tool_calls'}})
        self.completion = completion or CompletionMetadata()
        self.protocol_validator = protocol_validator
        self.protocol_validation = protocol_validation

    @property
    def content(self):
        return self.get('content')

    @property
    def tool_calls(self):
        return self.get('tool_calls') or []

    @property
    def finish_reason(self):
        return self.completion.finish_reason

    @property
    def input_tokens(self):
        return self.completion.input_tokens

    @property
    def output_tokens(self):
        return self.completion.output_tokens

    @property
    def requested_output_limit(self):
        return self.completion.requested_output_limit

    @property
    def provider_metadata(self):
        return self.completion.provider_metadata

    @property
    def elapsed_ms(self):
        return self.completion.elapsed_ms


class CompletionRejected(RuntimeError):
    def __init__(self, failure_type, details=''):
        self.failure_type = FailureType(failure_type)
        super().__init__(f'{self.failure_type}: {details or "Antwort nicht vollständig validiert."}')
