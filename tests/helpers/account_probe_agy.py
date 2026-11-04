"""Fake agy variants for the account isolation proof (D117).

The real unauthenticated ``agy -p "/usage"`` prints the auth marker and then
keeps running for its own login timeout. These variants model that, plus the
ordinary marker-and-exit, logged-in and silent-hang cases. Everything is fake:
no test runs the real agy or reads a credential file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from tests.helpers.account_cli import FAKE_EMAIL, usage_payload

# The marker the isolation proof requires, followed by a URL-shaped value that
# must never reach output or a log.
MARKER = "Authentication required. Please visit the URL to log in:"
URL_SENTINEL = "https://login.example.invalid/oauth?state=secret-state-sentinel"

_BEHAVIORS = {"marker_sleep", "marker_exit", "logged_in", "silent_hang"}


def write_probe_agy(
    path: Path,
    *,
    behavior: str = "marker_sleep",
    pid_file: Optional[Path] = None,
    email: str = FAKE_EMAIL,
) -> Path:
    """Write a fake agy that models one isolation-probe outcome.

    ``behavior`` applies only to an unauthenticated ``/usage`` probe:

    - ``marker_sleep``: print the marker + URL, then sleep 60 s (real agy).
    - ``marker_exit``: print the marker + URL on stderr, then exit 1.
    - ``logged_in``: print usage/quota JSON as if the keyring leaked.
    - ``silent_hang``: print nothing and sleep 60 s.

    A probe in a home that contains ``.fake_logged_in`` always answers as a
    logged-in session, and a non-``/usage`` launch writes that marker file, so
    one script covers both the proof and the injected login.
    """
    if behavior not in _BEHAVIORS:
        raise ValueError(f"unknown behavior {behavior!r}")
    marker_file = repr(str(pid_file)) if pid_file is not None else "None"
    script = f'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

EMAIL = {email!r}
BEHAVIOR = {behavior!r}
MARKER = {MARKER!r}
URL = {URL_SENTINEL!r}
PID_FILE = {marker_file}
PAYLOAD = {usage_payload()!r}

home = Path(os.environ["HOME"])
is_usage = "-p" in sys.argv and "/usage" in sys.argv

if not is_usage:
    (home / ".fake_logged_in").write_text("1", encoding="utf-8")
    sys.exit(0)

logged_in = BEHAVIOR == "logged_in" or (home / ".fake_logged_in").is_file()
if logged_in:
    if "--log-file" in sys.argv:
        log = Path(sys.argv[sys.argv.index("--log-file") + 1])
        log.write_text(
            "applyAuthResult: email=" + EMAIL + ", authMethod=consumer\\n",
            encoding="utf-8",
        )
    print(json.dumps(PAYLOAD))
    sys.exit(0)

if PID_FILE:
    Path(PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

if BEHAVIOR == "marker_sleep":
    sys.stdout.write(MARKER + " " + URL + "\\n")
    sys.stdout.flush()
    time.sleep(60)
    sys.exit(0)
if BEHAVIOR == "marker_exit":
    sys.stderr.write(MARKER + " " + URL + "\\n")
    sys.stderr.flush()
    sys.exit(1)
time.sleep(60)
sys.exit(0)
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path
