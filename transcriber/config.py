"""Configuration: defaults, optional TOML file, and --set overrides.

Stdlib-only (tomllib is stdlib on 3.11+; on older pythons the TOML file is
simply ignored — defaults and --set still work).
"""

from __future__ import annotations

import copy
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "meeting-recorder"
CONFIG_PATH = DATA_DIR / "transcribe.toml"

DEFAULTS = {
    "asr": {
        "model": "large-v3-turbo",
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 10,
        "beam_size": 5,
        "language": "en",
        "condition_on_previous_text": False,
        "vad_min_silence_ms": 500,
        "vad_speech_pad_ms": 200,
        "hotwords": "",
    },
    "quality": {
        # Render-time post-filters (tunable without re-running ASR)
        "no_speech_prob": 0.6,
        "avg_logprob": -1.0,
        "compression_ratio": 2.4,
        "blacklist_logprob": -0.5,
    },
    "echo": {
        "pad_s": 0.75,
        "overlap_min": 0.5,
        "full_ratio": 80,
        "set_ratio": 90,
        "partial_ratio": 85,
        "partial_min_words": 3,
    },
    "merge": {
        "turn_gap_s": 3.0,
        "paragraph_chars": 600,
        "drop_backchannels": False,
        # Insert '.' at word gaps >= this many seconds (0 = off)
        "pause_split_s": 0.5,
    },
}

HALLUCINATION_BLACKLIST = (
    "thanks for watching",
    "thank you for watching",
    "subtitles by",
    "please subscribe",
    "www.",
)


def load(overrides: list[str] | None = None) -> dict:
    """Return config: DEFAULTS <- transcribe.toml <- --set overrides."""
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            import tomllib

            with open(CONFIG_PATH, "rb") as fh:
                _deep_update(cfg, tomllib.load(fh))
        except Exception:
            pass  # unparseable/old python: defaults still apply
    for item in overrides or []:
        key, _, value = item.partition("=")
        _set_dotted(cfg, key.strip(), _coerce(value.strip()))
    return cfg


def _deep_update(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def _set_dotted(cfg: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _coerce(text: str):
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text
