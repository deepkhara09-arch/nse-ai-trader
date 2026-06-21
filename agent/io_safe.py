"""
Safe JSON load helpers — one place that guarantees a loader never crashes the
run on a missing, empty, corrupt, or wrong-type brain file.

Every brain file is committed by GitHub Actions and read on the next run. A
half-written file (interrupted commit), an empty {} written by a reset where a []
was expected, or a truncated download can otherwise raise on json.load or on the
first .append()/iteration. These helpers collapse all of that into a typed
default, so a bad file degrades to "start fresh" instead of taking the tool down.
"""

import json
import os
from typing import Any


def load_json_dict(path: str, default: dict = None) -> dict:
    """Load a JSON object. Returns {} (or given default) if missing/corrupt/not-a-dict."""
    default = {} if default is None else default
    if not os.path.exists(path):
        return dict(default)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def load_json_list(path: str, default: list = None) -> list:
    """Load a JSON array. Returns [] (or given default) if missing/corrupt/not-a-list."""
    default = [] if default is None else default
    if not os.path.exists(path):
        return list(default)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else list(default)
    except Exception:
        return list(default)


def load_json_any(path: str, default: Any = None) -> Any:
    """Load arbitrary JSON. Returns default if missing/corrupt."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default
