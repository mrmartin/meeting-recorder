"""faster-whisper ASR wrapper producing cacheable segment JSON."""

from __future__ import annotations

from pathlib import Path


def transcribe(wav: Path, speaker: str, cfg: dict, on_progress=None,
               duration: float | None = None) -> list[dict]:
    """Transcribe one mono WAV; returns segment dicts tagged with speaker.

    Heavy import is deferred so the CLI can print help without the venv.
    """
    from faster_whisper import WhisperModel

    a = cfg["asr"]
    model = _get_model(a)
    segments, info = model.transcribe(
        str(wav),
        language=a["language"],
        beam_size=a["beam_size"],
        word_timestamps=True,
        condition_on_previous_text=a["condition_on_previous_text"],
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=a["vad_min_silence_ms"],
            speech_pad_ms=a["vad_speech_pad_ms"],
        ),
        hotwords=a["hotwords"] or None,
    )
    total = duration or info.duration or 1.0
    out = []
    for seg in segments:
        out.append({
            "speaker": speaker,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "avg_logprob": round(seg.avg_logprob, 4),
            "no_speech_prob": round(seg.no_speech_prob, 4),
            "compression_ratio": round(seg.compression_ratio, 4),
            "words": [
                {"start": round(w.start, 3), "end": round(w.end, 3),
                 "word": w.word}
                for w in (seg.words or [])
            ],
        })
        if on_progress:
            on_progress(min(seg.end / total, 1.0))
    return out


_model_cache: dict = {}


def _get_model(a: dict):
    from faster_whisper import WhisperModel

    key = (a["model"], a["device"], a["compute_type"])
    if key not in _model_cache:
        _model_cache[key] = WhisperModel(
            a["model"], device=a["device"], compute_type=a["compute_type"],
            cpu_threads=a["cpu_threads"],
        )
    return _model_cache[key]
