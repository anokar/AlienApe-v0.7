"""Conservative recognition and root extraction for new multi-file requests."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ProjectRequest:
    goal: str
    root: str | None
    language_hint: str


def project_request(text):
    if not isinstance(text, str) or len(text) > 8192:
        return None
    t = ' '.join(text.strip().split())
    if re.search(r'(?i)\b(?:do not|don.t|nicht|kein(?:e[nsrm]?)?|nur analysieren|read.only)\b',t):
        return None
    if not re.search(r'(?i)\b(?:mach\w*|erstel\w*|baue|build|create|make)\b',t):
        return None
    project=bool(re.search(r'(?i)\b(?:projekt|project)\b',t) and re.search(r'(?i)\b(?:tests?|dateien|files?|modules?|module|mehrere|multiple|gui|pyqt6)\b',t))
    site=bool(re.search(r'(?i)\b(?:website|webseite|site)\b',t) and re.search(r'(?i)\bhtml\b',t) and re.search(r'(?i)\bcss\b',t) and re.search(r'(?i)\b(?:javascript|js)\b',t))
    if not (project or site):
        return None
    # Only explicit labels establish a root; filenames, goals and model output do not.
    match=re.search(r'(?i)\b(?:namens|named|called|in)\s+(\S+)',t)
    root=match.group(1).rstrip('.,!') if match else None
    language='python' if re.search(r'(?i)\b(?:python|pyqt6)\b',t) else 'html' if site else ''
    return ProjectRequest(t,root,language)
