"""Explicit file-view commands are data access, not a model inference task."""
import re


PATH = r'''(?:"(?P<double>[^"\r\n]+)"|'(?P<single>[^'\r\n]+)'|`(?P<code>[^`\r\n]+)`|„(?P<german>[^“\r\n]+)“|(?P<bare>[^\s"'`<>|?*]+))'''
PREFIXES = (
    r"(?:bitte\s+)?(?:lies|lese|öffne|read|open)\s+(?:(?:mir\s+)?(?:die\s+)?datei\s+|(?:the\s+)?file\s+)?",
    r"(?:bitte\s+)?(?:zeige|zeig|show|display)\s+(?:mir\s+)?(?:die\s+datei\s+|(?:den\s+)?inhalt\s+(?:von\s+|der\s+datei\s+)|(?:the\s+)?(?:file\s+)?contents?\s+of\s+)",
    r"was\s+steht\s+in\s+(?:der\s+datei\s+)?",
)


def direct_read_path(task):
    """Match an entire read/view request; extra analysis instructions must not disappear."""
    for prefix in PREFIXES:
        match = re.fullmatch(prefix + PATH + r"\s*(?:bitte\s*)?[.!?]*", task.strip(), re.I)
        if match:
            groups = match.groupdict()
            path = next(value for value in groups.values() if value is not None)
            return path.rstrip(".,!") if groups["bare"] is not None else path
    return None


def format_file_contents(path, content):
    if not content:
        return f"Die Datei „{path}“ ist leer."
    return f"Inhalt von „{path}“:\n\n{content}"
