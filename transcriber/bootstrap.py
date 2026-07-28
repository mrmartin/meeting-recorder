"""Venv bootstrap — STDLIB ONLY, runnable by any python3 >= 3.8.

Creates ~/.local/share/meeting-recorder/venv with CPU-only torch (the
CPU wheel index is used first so pyannote cannot drag in ~5 GB of CUDA
wheels), faster-whisper, pyannote.audio, and rapidfuzz.

Usage: python3 -m transcriber.bootstrap [--json]
"""

from __future__ import annotations

import json
import subprocess
import sys
import venv
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "meeting-recorder"
VENV_DIR = DATA_DIR / "venv"
STAMP = VENV_DIR / ".ok"
SCHEMA = 1

STEPS = [
    (["--index-url", "https://download.pytorch.org/whl/cpu",
      "torch", "torchaudio"], "PyTorch (CPU)"),
    (["faster-whisper"], "faster-whisper"),
    (["pyannote.audio", "rapidfuzz"], "pyannote + tools"),
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

    total = len(STEPS) + 1
    if not venv_python().exists():
        report("Creating environment…", 0 / total)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        venv.create(VENV_DIR, with_pip=True)
    pip = [str(venv_python()), "-m", "pip", "install", "--quiet",
           "--disable-pip-version-check"]
    for i, (args, label) in enumerate(STEPS, start=1):
        report(f"Downloading {label} ({i} of {len(STEPS)})…", i / total)
        result = subprocess.run(pip + args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Installing {label} failed:\n{result.stderr.strip()[-2000:]}"
            )
    STAMP.write_text(json.dumps({"schema": SCHEMA}))
    report("Setup complete.", 1.0)


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
