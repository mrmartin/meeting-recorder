"""Recording file storage: paths, filenames, listing."""

from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

from . import settings

DEFAULT_RECORDINGS_DIR = Path.home() / "Recordings"


def _chosen_dir(key: str) -> Path | None:
    chosen = settings.load()[key]
    return Path(chosen).expanduser() if chosen else None


def recordings_folder() -> Path:
    """The recordings folder, which may not exist (e.g. an unplugged drive)."""
    return _chosen_dir("recordings_dir") or DEFAULT_RECORDINGS_DIR


def recordings_dir() -> Path:
    """Return the recordings directory, creating it if needed."""
    folder = recordings_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def transcripts_dir() -> Path:
    """Folder new transcripts go to for recordings in the recordings folder."""
    return _chosen_dir("transcripts_dir") or recordings_folder()


def transcripts_reachable() -> bool:
    """False while a chosen transcripts folder is missing, e.g. an unplugged
    drive: its recordings would all look untranscribed."""
    chosen = _chosen_dir("transcripts_dir")
    return chosen is None or chosen.is_dir()


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
    folder = recordings_folder()
    if not folder.is_dir():
        return []
    files = [p for p in folder.glob("meeting_*.ogg") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _transcript_file(audio_path: Path, suffix: str) -> Path:
    """The chosen folder's file, or one already beside the recording: choosing
    a folder must not make earlier transcripts look missing."""
    name = audio_path.stem + suffix
    beside = audio_path.with_name(name)
    target = (_chosen_dir("transcripts_dir") or audio_path.parent) / name
    return beside if beside.is_file() and not target.is_file() else target


def transcript_path(audio_path: Path) -> Path:
    """Transcript file that belongs to a recording."""
    return _transcript_file(audio_path, ".transcript.md")


def transcript_files(folder: Path) -> list[Path]:
    """Every transcript in a folder, including local backups."""
    return sorted(folder.glob("meeting_*.transcript*.md"))


def move_transcripts(source: Path, dest: Path) -> None:
    """Move transcripts between folders, never replacing an existing file."""
    dest.mkdir(parents=True, exist_ok=True)
    for path in transcript_files(source):
        if not (dest / path.name).exists():
            shutil.move(str(path), dest / path.name)


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


def _header_engine(audio_path: Path) -> str | None:
    """Engine named in the transcript's own header — survives a purged
    .transcribe cache, e.g. "- **Transcribed:** … · mono-speech (cloud)"."""
    try:
        with transcript_path(audio_path).open(encoding="utf-8") as f:
            for _ in range(10):
                match = re.match(r"- \*\*Transcribed:\*\*.*\((\w+)\)",
                                 f.readline())
                if match:
                    return match.group(1)
    except OSError:
        pass
    return None


def transcript_engine(audio_path: Path) -> str | None:
    """Engine that produced the transcript currently opened by the app."""
    if not has_transcript(audio_path):
        return None
    engine = (_transcription_meta(audio_path).get("render", {}).get("engine")
              or _header_engine(audio_path))
    return "cloud" if engine == "cloud" else "local"


def local_transcript_backup_path(audio_path: Path) -> Path:
    """Backup retained when a local transcript is replaced by cloud output."""
    return _transcript_file(audio_path, ".transcript.local.md")


def has_cloud_transcript(audio_path: Path) -> bool:
    """Whether the active transcript is a completed cloud transcription."""
    return transcript_engine(audio_path) == "cloud"


def has_cloud_result(audio_path: Path) -> bool:
    """Whether this audio has ever completed both cloud channel requests."""
    if transcript_engine(audio_path) == "cloud":
        return True
    meta = _transcription_meta(audio_path)
    needed = {"mono"} if _looks_mono_cloud(meta) else {"Me", "Them"}
    return all(isinstance(meta.get(f"cloud.{name}"), dict) for name in needed)


def _looks_mono_cloud(meta: dict) -> bool:
    return "cloud.mono" in meta and not any(
        key in meta for key in ("cloud.Me", "cloud.Them"))


def _evident_engines(audio_path: Path) -> set[str]:
    """Engines the transcript files and the cache show as finished."""
    engines = set()
    active = transcript_engine(audio_path)
    if active:
        engines.add(active)
    if local_transcript_backup_path(audio_path).is_file():
        engines.add("local")
    if has_cloud_result(audio_path):
        engines.add("cloud")
    return engines


def _history_path() -> Path:
    """Record of finished transcriptions, kept with the settings so it
    outlives a cleared cache and moved, renamed or deleted transcripts."""
    return settings.SETTINGS_PATH.with_name("transcribed.json")


def _history() -> dict:
    try:
        value = json.loads(_history_path().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _remembered_engines(history: dict, audio_path: Path) -> set[str]:
    """Entries carry the file size: a new recording that reuses the name of
    a deleted one has not been transcribed."""
    entry = history.get(audio_path.name)
    try:
        if entry["size"] == audio_path.stat().st_size:
            return set(entry["engines"])
    except (OSError, KeyError, TypeError):
        pass
    return set()


def _remember(finished: dict[Path, set[str]]) -> None:
    """Best effort: without the record, the transcript files still count."""
    history = _history()
    changed = False
    try:
        for audio_path, engines in finished.items():
            known = _remembered_engines(history, audio_path)
            if engines - known:
                history[audio_path.name] = {
                    "size": audio_path.stat().st_size,
                    "engines": sorted(known | engines)}
                changed = True
        if changed:
            path = _history_path()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(history, indent=1) + "\n")
            tmp.replace(path)
    except OSError:
        pass


def remember_transcription(audio_path: Path, engine: str) -> None:
    """Record that a transcription of this recording has finished."""
    _remember({audio_path: {engine}})


def remember_existing() -> None:
    """Record every transcription the files on disk already show, so ones
    made before this record existed are covered too."""
    _remember({path: _evident_engines(path) for path in list_recordings()})


def already_transcribed(audio_path: Path, cloud: bool) -> bool:
    """Whether Transcribe all should leave this recording alone. With Cloud
    on that means a cloud result exists or once did; otherwise any result."""
    engines = (_evident_engines(audio_path)
               | _remembered_engines(_history(), audio_path))
    return "cloud" in engines if cloud else bool(engines)


def human_size(num_bytes: int) -> str:
    """Format a byte count for display, e.g. '12.4 MB'."""
    size = float(num_bytes)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{size:.1f} GB"
