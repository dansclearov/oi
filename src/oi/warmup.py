"""Background pre-import of pydantic_ai.

pydantic_ai's package `__init__` costs ~600ms, so the startup path keeps it
to function-local imports and the interactive frontends call `warm()` once
their UI is up, hiding the import behind the user's first pause.

Python resolves import cycles that span threads by exposing partially
initialized modules, and pydantic_ai has internal cycles — a plain import
racing the warm-up thread crashes with "partially initialized module".
Any code that can be the process's first pydantic_ai touch after `warm()`
was called must therefore go through `ensure()` (both frontends gate their
turn and image-paste paths on it; everything else runs downstream of those,
or before `warm()`).
"""

import threading

_lock = threading.Lock()


def ensure() -> None:
    """Import pydantic_ai, waiting for an in-flight warm-up instead of racing it."""
    with _lock:
        import pydantic_ai.direct  # noqa: F401


def warm() -> None:
    """Import pydantic_ai on a background thread."""
    threading.Thread(target=ensure, name="pydantic-ai-warmup", daemon=True).start()
