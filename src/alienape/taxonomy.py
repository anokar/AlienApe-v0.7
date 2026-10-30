"""Small, deterministic refinement of the existing task classification."""
from enum import StrEnum
import re


class TaskCategory(StrEnum):
    GENERAL_SIMPLE = 'GENERAL_SIMPLE'
    SUMMARIZATION = 'SUMMARIZATION'
    PYTHON_CODING = 'PYTHON_CODING'
    PYTHON_DEBUG = 'PYTHON_DEBUG'
    CODE_REVIEW = 'CODE_REVIEW'
    FILE_ANALYSIS = 'FILE_ANALYSIS'
    STRUCTURED_OUTPUT = 'STRUCTURED_OUTPUT'
    TOOL_USE = 'TOOL_USE'
    PLANNING = 'PLANNING'
    REASONING = 'REASONING'
    GODOT_CODE = 'GODOT_CODE'
    MEMORY = 'MEMORY'
    CODE_GENERATION = 'CODE_GENERATION'
    FILE_CREATION = 'FILE_CREATION'
    EXPLANATION = 'EXPLANATION'


def task_category(task, task_type=None):
    from .routing import classify_task, TaskType
    task_type = task_type or classify_task(task)[0]
    text = task.casefold()
    if task_type == TaskType.MEMORY_TASK:
        return TaskCategory.MEMORY
    if re.search(r'\b(tool|werkzeug\w*)\b', text):
        return TaskCategory.TOOL_USE
    if re.search(r'\b(zusammenfass\w*|fasse|summari\w*)\b', text):
        return TaskCategory.SUMMARIZATION
    if task_type == TaskType.CODE_TASK:
        if re.search(r'\b(godot|gdscript)\b|\.gd\b', text):
            return TaskCategory.GODOT_CODE
        if re.search(r'\b(review|code_review)\b|code.prüfung', text):
            return TaskCategory.CODE_REVIEW
        if re.search(r'\b(python|py)\b', text):
            return TaskCategory.PYTHON_DEBUG if re.search(r'\b(debug\w*|fehler\w*|bug\w*|reparier\w*|fix\w*)\b', text) else TaskCategory.PYTHON_CODING
        return TaskCategory.CODE_REVIEW
    if task_type == TaskType.REASONING_TASK:
        return TaskCategory.PLANNING if re.search(r'\b(plan\w*)\b', text) else TaskCategory.REASONING
    if task_type == TaskType.FILE_TASK:
        return TaskCategory.FILE_ANALYSIS
    return TaskCategory.GENERAL_SIMPLE
