"""Audio inspection and preparation via ffprobe/ffmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class AudioInfo:
    def __init__(self, channels: int, duration: float):
        self.channels = channels
        self.duration = duration

    @property
    def is_stereo(self) -> bool:
        return self.channels == 2


def probe(path: Path) -> AudioInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels", "-show_entries",
         "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    return AudioInfo(
        channels=int(data["streams"][0]["channels"]),
        duration=float(data["format"]["duration"]),
    )


def split_stereo(src: Path, left_out: Path, right_out: Path) -> None:
    """Split stereo into two 16 kHz mono WAVs (L=mic/Me, R=system/Them)."""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
          "-filter_complex", "[0:a]channelsplit=channel_layout=stereo[l][r]",
          "-map", "[l]", "-ar", "16000", "-c:a", "pcm_s16le", str(left_out),
          "-map", "[r]", "-ar", "16000", "-c:a", "pcm_s16le", str(right_out)])


def to_mono_wav(src: Path, out: Path) -> None:
    """Decode any input to one 16 kHz mono WAV (legacy mixed recordings)."""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)])


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
