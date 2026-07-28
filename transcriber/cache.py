"""Work-directory layout and stage caching.

Each recording gets ~/Recordings/.transcribe/<stem>/ holding channel WAVs,
ASR/diarization JSON, drop logs, and meta.json. Stages skip themselves when
their outputs exist and are newer than their inputs (unless --force).
"""

from __future__ import annotations

import json
from pathlib import Path


def work_dir(audio_path: Path) -> Path:
    d = audio_path.parent / ".transcribe" / audio_path.stem
    d.mkdir(parents=True, exist_ok=True)
    return d


def fresh(outputs: list[Path], inputs: list[Path]) -> bool:
    """True if all outputs exist and are newer than every input."""
    if not all(p.exists() for p in outputs):
        return False
    newest_input = max(p.stat().st_mtime for p in inputs)
    return all(p.stat().st_mtime >= newest_input for p in outputs)


def read_json(path: Path):
    with open(path) as fh:
        return json.load(fh)


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    tmp.replace(path)


def update_meta(work: Path, key: str, value) -> None:
    meta_path = work / "meta.json"
    meta = read_json(meta_path) if meta_path.exists() else {}
    meta[key] = value
    write_json(meta_path, meta)
