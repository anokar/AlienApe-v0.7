"""Small deterministic post-output validators. No repair, model judge or tool execution."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import re
from jsonschema import validate as check_schema, ValidationError
from .completion import FailureType, ModelResponse


@dataclass
class ValidationResult:
    valid: bool | None = None
    complete: bool | None = None
    verified: bool = False
    failure_type: FailureType = FailureType.UNKNOWN
    details: dict = field(default_factory=dict)
    scope: str = 'unknown'


class ResultValidator(ABC):
    @abstractmethod
    def validate(self, task, response) -> ValidationResult: ...


def content(response):
    return response.get('content') if isinstance(response,dict) else response


class JSONValidator(ResultValidator):
    def validate(self,task,response):
        try:
            value = json.loads(content(response))
        except (ValueError,TypeError):
            return ValidationResult(False,False,True,FailureType.FORMAT_FAILURE,{'valid_json':False},'format')
        return ValidationResult(True,True,True,FailureType.SUCCESS,{'valid_json':True},'format')


class JSONSchemaValidator(JSONValidator):
    def __init__(self,schema):
        self.schema = schema

    def validate(self,task,response):
        parsed = super().validate(task,response)
        if not parsed.valid:
            return parsed
        value = json.loads(content(response))
        try:
            check_schema(value,self.schema)
        except ValidationError as exc:
            missing = exc.validator=='required'
            return ValidationResult(False,not missing,True,FailureType.VALIDATION_FAILURE,
                                    {'valid_json':True,'schema_valid':False,'constraint':exc.validator},'schema')
        return ValidationResult(True,True,True,FailureType.SUCCESS,{'valid_json':True,'schema_valid':True},'schema')


class ExactValueValidator(JSONValidator):
    def __init__(self,expected):
        self.expected = expected

    def validate(self,task,response):
        parsed = super().validate(task,response)
        if not parsed.valid:
            parsed.scope = 'exact_value'
            return parsed
        value = json.loads(content(response))
        def equal(a,b):
            if type(a) is not type(b):
                return False
            if isinstance(b,dict):
                return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in b)
            if isinstance(b,list):
                return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
            return a==b
        complete = set(self.expected) <= set(value) if isinstance(self.expected,dict) and isinstance(value,dict) else type(value) is type(self.expected)
        okay = equal(value,self.expected)
        return ValidationResult(okay,complete,True,FailureType.SUCCESS if okay else FailureType.VALIDATION_FAILURE,
            {'valid_json':True,'required_keys_complete':complete,'values_correct':okay},'exact_value')


def validator_for_task(task):
    # Explicit output requirement only. No Godot-specific hardcoded semantic answers.
    from .contracts import requests_json
    if requests_json(task):
        return JSONValidator()
    return None
