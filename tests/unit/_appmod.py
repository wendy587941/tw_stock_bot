"""Load a Lambda handler module by function name.

All six functions live in ``src/<name>/app.py`` — same basename — so a plain
``import app`` would collide. This loads each under a unique module name
(``<name>_app``) via importlib and caches it.
"""

import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
_cache: dict = {}


def load_app(name: str):
    """Import ``src/<name>/app.py`` as module ``<name>_app`` (cached)."""
    if name in _cache:
        return _cache[name]
    path = _SRC / name / "app.py"
    spec = importlib.util.spec_from_file_location(f"{name}_app", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _cache[name] = mod
    return mod
