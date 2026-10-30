"""Small static-message catalog; never translates model output."""
from functools import lru_cache
from pathlib import Path
import json,locale

@lru_cache(maxsize=3)
def catalog(language):
    return json.loads((Path(__file__).parent/(language+".json")).read_text(encoding="utf-8"))

def effective_locale(language="system"):
    if language=="system":
        language=locale.getlocale()[0] or "en"
    base=language.replace("-","_").split("_")[0].lower()
    return base if base in {"de","en","fr"} else "en"

def localize(message_id,language="system",**params):
    selected=catalog(effective_locale(language))
    value=selected.get(message_id) or catalog("en").get(message_id) or catalog("de").get(message_id)
    if value is None:return "Message unavailable."
    try:return value.format(**params)
    except (KeyError,ValueError):return catalog("en").get("MESSAGE_UNAVAILABLE","Message unavailable.")
