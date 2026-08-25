"""Shared test setup for the job-os-agents function.

Workspace.__init__ (main.py) reads these two straight from os.environ with no
default, because in production Appwrite always injects them. Nothing here
talks to that endpoint or project (every test replaces workspace.tables with
a fake before calling anything that would), so any placeholder value works;
setdefault so a real value already in the environment is not clobbered.
"""
from __future__ import annotations

import os

os.environ.setdefault("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.test/v1")
os.environ.setdefault("APPWRITE_FUNCTION_PROJECT_ID", "test-project")
