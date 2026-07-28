"""Default audio device discovery via pactl (works on PulseAudio and PipeWire)."""

from __future__ import annotations

import shutil
import subprocess

# Special libpulse device aliases — resolved by the sound server itself,
# so they work on both PulseAudio and pipewire-pulse.
DEFAULT_MONITOR = "@DEFAULT_MONITOR@"
DEFAULT_SOURCE = "@DEFAULT_SOURCE@"


def _pactl(*args: str) -> str | None:
    if not shutil.which("pactl"):
        return None
    try:
        out = subprocess.run(
            ["pactl", *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    result = out.stdout.strip()
    return result if out.returncode == 0 and result else None


def default_sink() -> str | None:
    """Name of the default output sink, or None if unavailable."""
    return _pactl("get-default-sink")


def default_source() -> str | None:
    """Name of the default input source, or None if unavailable."""
    return _pactl("get-default-source")


def default_monitor() -> str | None:
    """Monitor source name of the default sink, or None if unavailable."""
    sink = default_sink()
    return f"{sink}.monitor" if sink else None


def audio_server_available() -> bool:
    """True if a Pulse-compatible sound server with a default sink is reachable."""
    return default_sink() is not None
