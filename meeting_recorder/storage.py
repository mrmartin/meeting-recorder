"""Recording file storage: paths, filenames, listing."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

RECORDINGS_DIR = Path.home() / "Recordings"


def recordings_dir() -> Path:
    """Return the recordings directory, creating it if needed."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def new_recording_path() -> Path:
    """Generate a unique path for a new recording."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = recordings_dir() / f"meeting_{stamp}.ogg"
    # Avoid clobbering if two recordings start within the same minute
    n = 2
    while path.exists():
        path = recordings_dir() / f"meeting_{stamp}_{n}.ogg"
        n += 1
    return path


def list_recordings() -> list[Path]:
    """Return existing recordings, newest first."""
    if not RECORDINGS_DIR.is_dir():
        return []
    files = [p for p in RECORDINGS_DIR.glob("meeting_*.ogg") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def transcript_path(audio_path: Path) -> Path:
    """Transcript file that belongs to a recording."""
    return audio_path.with_name(audio_path.stem + ".transcript.md")


def has_transcript(audio_path: Path) -> bool:
    return transcript_path(audio_path).is_file()


def _transcription_meta(audio_path: Path) -> dict:
    meta = (audio_path.parent / ".transcribe" / audio_path.stem /
            "meta.json")
    try:
        value = json.loads(meta.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def transcript_engine(audio_path: Path) -> str | None:
    """Engine that produced the transcript currently opened by the app."""
    if not has_transcript(audio_path):
        return None
    engine = _transcription_meta(audio_path).get("render", {}).get("engine")
    return "cloud" if engine == "cloud" else "local"


def local_transcript_backup_path(audio_path: Path) -> Path:
    """Backup retained when a local transcript is replaced by cloud output."""
    return audio_path.with_name(audio_path.stem + ".transcript.local.md")


def has_cloud_transcript(audio_path: Path) -> bool:
    """Whether the active transcript is a completed cloud transcription."""
    return transcript_engine(audio_path) == "cloud"


def has_cloud_result(audio_path: Path) -> bool:
    """Whether this audio has ever completed both cloud channel requests."""
    meta = _transcription_meta(audio_path)
    if meta.get("render", {}).get("engine") == "cloud":
        return True
    needed = {"mono"} if _looks_mono_cloud(meta) else {"Me", "Them"}
    return all(isinstance(meta.get(f"cloud.{name}"), dict) for name in needed)


def _looks_mono_cloud(meta: dict) -> bool:
    return "cloud.mono" in meta and not any(
        key in meta for key in ("cloud.Me", "cloud.Them"))


def has_local_transcript(audio_path: Path) -> bool:
    """Whether a local result is active or retained as the cloud backup."""
    return (local_transcript_backup_path(audio_path).is_file()
            or (has_transcript(audio_path)
                and not has_cloud_transcript(audio_path)))


def human_size(num_bytes: int) -> str:
    """Format a byte count for display, e.g. '12.4 MB'."""
    size = float(num_bytes)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{size:.1f} GB"
