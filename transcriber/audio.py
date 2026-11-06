"""Audio inspection and preparation via ffprobe/ffmpeg."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from array import array
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


def plan_parts(wav_path: Path, max_s: float,
               search_s: float) -> list[tuple[float, float]]:
    """Divide a 16-bit mono WAV into even (start, end) parts in seconds.

    Parts aim for at most max_s; each cut then moves to the quietest moment
    within search_s, so it falls in a pause rather than mid-word.
    """
    with wave.open(str(wav_path), "rb") as reader:
        duration = reader.getnframes() / reader.getframerate()
        count = max(1, math.ceil(duration / max_s))
        cuts = [_quietest(reader, duration * i / count, search_s)
                for i in range(1, count)]
    edges = [0.0, *cuts, duration]
    return list(zip(edges, edges[1:]))


def _quietest(reader, target_s: float, search_s: float) -> float:
    """Middle of the quietest half second around target_s, in seconds."""
    rate = reader.getframerate()
    first = max(0, round((target_s - search_s) * rate))
    reader.setpos(first)
    samples = array("h", reader.readframes(round(2 * search_s * rate)))
    if sys.byteorder == "big":
        samples.byteswap()
    step = rate // 10
    energy = [sum(s * s for s in samples[i:i + step])
              for i in range(0, len(samples) - step + 1, step)]
    runs = [sum(energy[i:i + 5]) for i in range(len(energy) - 4)]
    middle = (target_s * rate - first) / step - 2.5
    best = min(range(len(runs)),
               key=lambda i: (runs[i], abs(i - middle)))
    return (first + (best + 2.5) * step) / rate


def extract_part(wav_path: Path, start_s: float, end_s: float,
                 out: Path) -> None:
    """Copy one sample-exact span of a WAV to its own file."""
    with wave.open(str(wav_path), "rb") as src, \
            wave.open(str(out), "wb") as dst:
        rate = src.getframerate()
        dst.setparams(src.getparams())
        first = round(start_s * rate)
        src.setpos(first)
        remaining = round(end_s * rate) - first
        while remaining > 0:
            count = min(remaining, 60 * rate)
            dst.writeframes(src.readframes(count))
            remaining -= count


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
