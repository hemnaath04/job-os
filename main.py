"""Repository-root entrypoint for the Appwrite ``job-os-agents`` function.

Appwrite's git runtime resolves the function entrypoint relative to the
configured root directory. That root is the repo root (``./``) here, which is
required so the build step can ``pip install ./apps/api`` (the shared
``job_os`` package). In that setup Appwrite looks for the entrypoint file at
the repo root and does not reliably follow a subdirectory entrypoint path, so
this thin shim lives at the root and loads the real handler from
``apps/functions/job-os-agents/main.py`` by file path.

The function directory name contains a dash, so it cannot be imported as a
normal Python module; ``importlib`` loads it by absolute path instead. The
build still installs everything the real handler imports (anthropic, appwrite,
langgraph, the ``job_os`` package), so executing it here works unchanged.
"""
from __future__ import annotations

import importlib.util
import os
from typing import Any

_HANDLER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "apps",
    "functions",
    "job-os-agents",
    "main.py",
)

_spec = importlib.util.spec_from_file_location("job_os_agents_handler", _HANDLER_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise RuntimeError(f"Could not load the agent handler at {_HANDLER_PATH}")
_handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_handler)


async def main(context: Any) -> Any:
    """Delegate to the real Appwrite Function handler."""
    return await _handler.main(context)
