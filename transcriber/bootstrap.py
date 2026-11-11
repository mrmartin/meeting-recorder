"""Venv bootstrap — STDLIB ONLY, runnable by any python3 >= 3.8.

Creates ~/.local/share/meeting-recorder/venv with faster-whisper
(CTranslate2 — no torch, no CUDA) and rapidfuzz. Only ready-built packages
are installed: compiling one needs developer tools a desktop lacks.

Usage: python3 -m transcriber.bootstrap [--json]
"""

from __future__ import annotations

import json
import subprocess
import sys
import venv
from pathlib import Path

from . import sysdeps

DATA_DIR = Path.home() / ".local" / "share" / "meeting-recorder"
VENV_DIR = DATA_DIR / "venv"
STAMP = VENV_DIR / ".ok"
SCHEMA = 2

NETWORK_SIGNS = ("NewConnectionError", "Temporary failure in name resolution",
                 "Max retries exceeded", "ReadTimeoutError")

NO_BUILD_SIGNS = ("No matching distribution", "ResolutionImpossible")

STEPS = [
    (["faster-whisper>=1.1"], "speech recognition"),
    (["rapidfuzz"], "text tools"),
]


def venv_python() -> Path:
    return VENV_DIR / "bin" / "python"


def venv_ready() -> bool:
    if not STAMP.is_file():
        return False
    try:
        return json.loads(STAMP.read_text()).get("schema") == SCHEMA
    except (ValueError, OSError):
        return False


def run(emit=None) -> None:
    """Create the venv and install everything. emit(stage_msg, progress)."""
    def report(msg, progress):
        if emit:
            emit(msg, progress)
        else:
            print(f"==> {msg}", file=sys.stderr)

    sysdeps.require("venv", "ffmpeg")
    total = len(STEPS) + 1
    if not venv_python().exists():
        report("Creating environment…", 0 / total)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        venv.create(VENV_DIR, with_pip=True)
    pip = [str(venv_python()), "-m", "pip", "install", "--quiet",
           "--disable-pip-version-check", "--only-binary=:all:"]
    for i, (args, label) in enumerate(STEPS, start=1):
        report(f"Downloading {label} ({i} of {len(STEPS)})…", i / total)
        result = subprocess.run(pip + args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(_failure(label, result.stderr))
    STAMP.write_text(json.dumps({"schema": SCHEMA}))
    report("Setup complete.", 1.0)


def _failure(label: str, pip_output: str) -> str:
    """Explain a failed download in terms the user can act on."""
    if any(sign in pip_output for sign in NETWORK_SIGNS):
        return (f"Could not download {label}. Check your internet "
                "connection and try again.")
    if any(sign in pip_output for sign in NO_BUILD_SIGNS):
        version = ".".join(map(str, sys.version_info[:2]))
        return (f"Local transcription cannot be set up on this system yet: "
                f"{label} has no build for Python {version}.")
    return f"Installing {label} failed:\n{pip_output.strip()[-2000:]}"


if __name__ == "__main__":
    json_mode = "--json" in sys.argv
    try:
        if venv_ready():
            print("venv already up to date", file=sys.stderr)
        else:
            if json_mode:
                run(emit=lambda m, p: print(json.dumps(
                    {"event": "stage", "stage": "bootstrap",
                     "message": m, "progress": p}), flush=True))
            else:
                run()
    except Exception as exc:  # noqa: BLE001
        if json_mode:
            print(json.dumps({"event": "error", "message": str(exc)}),
                  flush=True)
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
