"""Loads prompt files from prompts/ (or PROMPTS_DIR). See prompts/README.md."""

import os
import string
from pathlib import Path

PROMPTS_DIR = Path(os.getenv("PROMPTS_DIR") or Path(__file__).resolve().parent.parent / "prompts")

# file name -> placeholders it must contain (system prompts have none and are used verbatim)
REQUIRED = {
    "technical_system": set(),
    "fundamentals_system": set(),
    "news_system": set(),
    "macro_system": set(),
    "orchestrator_system": set(),
    "research_user": {"ticker", "today"},
    "orchestrator_user": {"ticker", "today", "reports"},
}


def _load(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    try:
        # Normalize whitespace so editor differences (trailing newline, CRLF) don't change the bytes
        # sent to the API, which would break prompt caching
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    except FileNotFoundError:
        raise RuntimeError(f"Prompt file missing: {path}") from None
    if not text:
        raise RuntimeError(f"Prompt file is empty: {path}")
    if REQUIRED[name]:
        found = {field for _, field, _, _ in string.Formatter().parse(text) if field}
        if found != REQUIRED[name]:
            raise RuntimeError(f"{path} must use exactly these placeholders: "
                               f"{sorted(REQUIRED[name])} (found {sorted(found)})")
    return text


# Loaded once at import so a broken prompt file stops the app at startup, not mid-analysis
PROMPTS = {name: _load(name) for name in REQUIRED}


def system(name: str) -> str:
    return PROMPTS[f"{name}_system"]


def render(name: str, **values: str) -> str:
    return PROMPTS[name].format(**values)
