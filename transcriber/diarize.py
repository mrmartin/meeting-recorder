"""Speaker diarization for legacy mixed-mono recordings (pyannote 3.1)."""

from __future__ import annotations

import os
from pathlib import Path

TOKEN_PATH = Path.home() / ".local" / "share" / "meeting-recorder" / "hf_token"
MODEL = "pyannote/speaker-diarization-3.1"


def hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    if TOKEN_PATH.is_file():
        return TOKEN_PATH.read_text().strip() or None
    return None


def diarize(wav: Path, num_speakers: int = 2) -> list[dict]:
    """Run diarization; returns [{start, end, speaker: 'SPEAKER_00'}, ...]."""
    token = hf_token()
    if not token:
        raise RuntimeError(
            "needs_hf_token: A free HuggingFace token is required for "
            "speaker identification on this older recording. Get one at "
            "hf.co/settings/tokens (accept terms at "
            "hf.co/pyannote/speaker-diarization-3.1 and "
            "hf.co/pyannote/segmentation-3.0)."
        )
    import torch  # noqa: F401 - ensures actionable error if venv is broken
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(MODEL, use_auth_token=token)
    annotation = pipeline(str(wav), num_speakers=num_speakers)
    turns = []
    for segment, _track, label in annotation.itertracks(yield_label=True):
        turns.append({"start": round(segment.start, 3),
                      "end": round(segment.end, 3), "speaker": label})
    turns.sort(key=lambda t: t["start"])
    return turns


def assign_speakers(segments: list[dict], diar_turns: list[dict]) -> list[dict]:
    """Split ASR segments at word level by diarization turn ownership.

    Each word goes to the diarization turn covering its midpoint (nearest
    turn within 1 s for words in gaps, else the previous word's speaker).
    Returns new segments with 'speaker' set to Speaker 1/Speaker 2/...,
    numbered by order of first appearance.
    """
    label_map: dict[str, str] = {}

    def pretty(label: str) -> str:
        if label not in label_map:
            label_map[label] = f"Speaker {len(label_map) + 1}"
        return label_map[label]

    out = []
    prev_label = diar_turns[0]["speaker"] if diar_turns else "SPEAKER_00"
    for seg in segments:
        words = seg["words"] or [{"start": seg["start"], "end": seg["end"],
                                  "word": seg["text"]}]
        current = None
        for word in words:
            mid = (word["start"] + word["end"]) / 2
            label = _owner(mid, diar_turns) or prev_label
            prev_label = label
            if current and current["label"] == label:
                current["words"].append(word)
            else:
                current = {"label": label, "words": [word]}
                out.append(current)
    result = []
    for chunk in out:
        text = "".join(w["word"] for w in chunk["words"]).strip()
        if not text:
            continue
        result.append({
            "speaker": pretty(chunk["label"]),
            "start": chunk["words"][0]["start"],
            "end": chunk["words"][-1]["end"],
            "text": text,
        })
    return result


def _owner(t: float, turns: list[dict], max_gap: float = 1.0) -> str | None:
    best, best_dist = None, max_gap
    for turn in turns:
        if turn["start"] <= t <= turn["end"]:
            return turn["speaker"]
        dist = turn["start"] - t if t < turn["start"] else t - turn["end"]
        if dist < best_dist:
            best, best_dist = turn["speaker"], dist
    return best
