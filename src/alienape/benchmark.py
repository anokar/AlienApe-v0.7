"""Explicit, bounded local comparisons. Model output is never executed or persisted."""
from dataclasses import dataclass
import ast
import asyncio
import json
import operator
import re
import threading
import time
import uuid
from jsonschema import validate
from .backends import MockBackend, tool_reply
from .context import size
from .experience import ModelFingerprint, SuccessSignal, SUITE_VERSION
from .routing import BackendPool
from .taxonomy import TaskCategory as C


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    category: str
    prompt: str
    validator: str
    expected: object


def suite(categories, scope='short'):
    """Same immutable tasks and order for every selected model; no project data."""
    tasks = []
    for category in categories:
        category = C(category)
        count = 1 if scope == 'short' else 3 if category in {C.PYTHON_CODING, C.PYTHON_DEBUG} else 2
        for i in range(count):
            identifier = f'v{SUITE_VERSION}-{category}-{i}'
            if category in {C.PYTHON_CODING, C.PYTHON_DEBUG}:
                operation, symbol = [('addition', '+'), ('subtraction', '-'), ('multiplication', '*')][i % 3]
                request = f'Write Python function solve(a, b) returning the {operation} of a and b.'
                if category == C.PYTHON_DEBUG:
                    wrong = '*' if symbol != '*' else '+'
                    request = f'Fix this Python bug: def solve(a, b): return a {wrong} b\nRequired behavior: {operation} of a and b.'
                tasks.append(BenchmarkTask(identifier, category, request + '\nReturn only Python code. No imports or external calls.', 'python', symbol))
            elif category == C.STRUCTURED_OUTPUT:
                expected = {'name': 'Ada', 'count': 3 + i}
                tasks.append(BenchmarkTask(identifier, category, f'Return only JSON: name="Ada", count={3+i}. Exactly these two keys.', 'json', expected))
            elif category == C.TOOL_USE:
                tasks.append(BenchmarkTask(identifier, category, 'Call read_file with exactly {"path":"fixture.txt"}. Do not answer in text. This is a synthetic benchmark; no file access will be performed.', 'tool', {'path': 'fixture.txt'}))
            elif category == C.SUMMARIZATION:
                tasks.append(BenchmarkTask(identifier, category, 'Summarize in one short sentence: Ada planted three trees on Monday. Rain watered the garden on Tuesday.', 'unrated', None))
            else:
                prompt, expected = {
                    C.GENERAL_SIMPLE: ('What is 7 + 5? Return only the integer.', '12'),
                    C.FILE_ANALYSIS: ('Fixture file: {"owner":"Ada","items":7}. Return only the owner value.', 'Ada'),
                    C.CODE_REVIEW: ('Review: def divide(a,b): return a*b. It should divide a by b. Return only the correct operator character.', '/'),
                    C.PLANNING: ('Dependencies: A before B; B before C. Return only the ordered IDs separated by commas.', 'A,B,C'),
                    C.REASONING: ('All glips are green. Nox is a glip. What color is Nox? Return only the English color.', 'green'),
                    C.GODOT_CODE: ('Godot 4 GDScript: name the 2D character class replacing KinematicBody2D. Return only the class name.', 'CharacterBody2D'),
                }[category]
                tasks.append(BenchmarkTask(identifier, category, prompt, 'exact', expected))
    return tasks


def plain(text):
    text = (text or '').strip()
    if text.startswith('```') and text.endswith('```'):
        return text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    return text


def check_python(text, symbol):
    """Unit inputs evaluated with a tiny arithmetic AST interpreter, never exec/eval.

    No imports, calls, loops, attributes, subscripts, allocations or file/network access.
    The strict fixture intentionally tests basic arithmetic functions, not general Python.
    """
    if len(text) > 16000:
        return False
    tree = ast.parse(plain(text))
    if len(list(ast.walk(tree))) > 100 or len(tree.body) != 1:
        return False
    fn = tree.body[0]
    if not isinstance(fn, ast.FunctionDef) or fn.name != 'solve' or fn.decorator_list:
        return False
    args = fn.args
    if len(args.args) != 2 or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults:
        return False
    body = fn.body
    if len(body) == 2 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
    def interpret(node, bindings, depth=0):
        if depth > 12:
            raise ValueError('AST too deep')
        if isinstance(node, ast.Name) and node.id in bindings:
            return bindings[node.id]
        if isinstance(node, ast.Constant) and type(node.value) in {int, float} and abs(node.value) < 100000:
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            result = binary[type(node.op)](interpret(node.left, bindings, depth+1), interpret(node.right, bindings, depth+1))
            if abs(result) > 1e12:
                raise ValueError('Arithmetic bound')
            return result
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -interpret(node.operand, bindings, depth+1)
        raise ValueError('Unsupported Python node')
    expected = {'+': operator.add, '-': operator.sub, '*': operator.mul}[symbol]
    for a, b in [(0, 0), (3, 2), (-4, 7), (12, -3), (2.5, 0.5)]:
        if interpret(body[0].value, {args.args[0].arg: a, args.args[1].arg: b}) != expected(a, b):
            return False
    return True


TOOL_SCHEMA = [{'type': 'function', 'function': {'name': 'read_file', 'description': 'Read a synthetic benchmark fixture; no execution.',
    'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path'], 'additionalProperties': False}}}]


def validate_response(task, response, mode='none'):
    if task.validator == 'unrated':
        return SuccessSignal.UNKNOWN
    try:
        if task.validator == 'tool':
            if mode == 'none':
                return SuccessSignal.UNKNOWN
            calls = response.get('tool_calls', [])
            okay = len(calls) == 1 and calls[0]['function']['name'] == 'read_file' and json.loads(calls[0]['function']['arguments']) == task.expected
        else:
            if response.get('tool_calls') or not isinstance(response.get('content'), str):
                return SuccessSignal.VERIFIED_FAILURE
            text = plain(response['content'])
            if task.validator == 'python':
                okay = check_python(text, task.expected)
            elif task.validator == 'json':
                value = json.loads(text)
                validate(value, {'type': 'object', 'properties': {'name': {'const': 'Ada'}, 'count': {'type': 'integer', 'const': task.expected['count']}},
                                 'required': ['name', 'count'], 'additionalProperties': False})
                okay = value == task.expected
            else:
                okay = text.casefold() == str(task.expected).casefold()
        return SuccessSignal.VERIFIED_SUCCESS if okay else SuccessSignal.VERIFIED_FAILURE
    except Exception:
        return SuccessSignal.VERIFIED_FAILURE


class BenchmarkDemoBackend(MockBackend):
    """Labelled demo with deterministic fixtures; never used for real providers."""
    async def chat_with_mode(self, messages, tools=None, model='', mode='none'):
        await asyncio.sleep(self.delay)
        prompt = messages[-1]['content']
        if tools and mode != 'none':
            return tool_reply('read_file', {'path': 'fixture.txt'})
        for operation, symbol in [('addition', '+'), ('subtraction', '-'), ('multiplication', '*')]:
            if operation in prompt:
                return {'role': 'assistant', 'content': f'def solve(a, b):\n    return a {symbol} b'}
        if 'Return only JSON' in prompt:
            return {'role': 'assistant', 'content': json.dumps({'name': 'Ada', 'count': int(re.search(r'count=(\d+)', prompt)[1])})}
        answers = [('owner value', 'Ada'), ('operator character', '/'), ('ordered IDs', 'A,B,C'), ('color is Nox', 'green'),
                   ('Godot 4', 'CharacterBody2D'), ('7 + 5', '12')]
        return {'role': 'assistant', 'content': next((value for key, value in answers if key in prompt), 'Ada planted trees and rain watered them.')}


# Legacy validators above remain public; the GUI and runner use compact suite 3.
from .training import TrainingRunner as BenchmarkRunner
