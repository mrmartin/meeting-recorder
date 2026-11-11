"""Check for system packages transcription needs, and name the command that
installs a missing one. Standard library only: the setup step imports this
before anything else exists."""

from __future__ import annotations

import importlib.util
import shutil

CHECKS = {
    "ffmpeg": lambda: bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
    # Debian and Ubuntu ship this part of Python as a separate package.
    "venv": lambda: importlib.util.find_spec("ensurepip") is not None,
}
DESCRIPTIONS = {
    "ffmpeg": "ffmpeg (audio conversion)",
    "venv": "Python's virtual environment support",
}
# Package manager -> (install command, package name for each need).
MANAGERS = {
    "apt": ("sudo apt install", {"ffmpeg": "ffmpeg", "venv": "python3-venv"}),
    "dnf": ("sudo dnf install", {"ffmpeg": "ffmpeg-free"}),
    "pacman": ("sudo pacman -S", {"ffmpeg": "ffmpeg"}),
    "zypper": ("sudo zypper install", {"ffmpeg": "ffmpeg"}),
}


def require(*needs: str) -> None:
    """Raise RuntimeError with install instructions if a need is missing."""
    missing = [need for need in needs if not CHECKS[need]()]
    if missing:
        raise RuntimeError(_instructions(missing))


def _instructions(missing: list[str]) -> str:
    what = " and ".join(DESCRIPTIONS[need] for need in missing)
    verb = "is" if len(missing) == 1 else "are"
    text = f"Transcription needs {what}, which {verb} not installed."
    for manager, (command, names) in MANAGERS.items():
        if shutil.which(manager) and all(need in names for need in missing):
            packages = " ".join(names[need] for need in missing)
            return (f"{text}\n\nOpen a terminal and run:\n\n"
                    f"    {command} {packages}\n\nThen try again.")
    return (f"{text}\n\nInstall with your system's package manager, "
            "then try again.")
