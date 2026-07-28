"""Markdown transcript rendering — meeting-minutes style."""

from __future__ import annotations

import datetime
import re
from pathlib import Path

_sentence_re = re.compile(r"(?<=[.?!]) +")


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render(turns: list[dict], audio_path: Path, duration: float,
           speakers: list[str], model: str, cfg: dict) -> str:
    date_label = _date_from_name(audio_path.stem)
    mins, secs = divmod(int(duration), 60)
    lines = [
        f"# Meeting transcript — {date_label}",
        "",
        f"- **Recording:** {audio_path.name}",
        f"- **Duration:** {mins} min {secs} s",
        f"- **Participants:** {', '.join(speakers)}",
        f"- **Transcribed:** {datetime.date.today().isoformat()} · "
        f"{model} (local)",
        "",
        "---",
        "",
    ]
    max_chars = cfg["merge"]["paragraph_chars"]
    for turn in turns:
        text = _tidy_case(" ".join(turn["texts"]).strip())
        paragraphs = _paragraphs(text, max_chars)
        label = f" {turn['speaker']}:" if turn["speaker"] else ""
        stamp = f"**[{hms(turn['start'])}]{label}**"
        lines.append(f"{stamp} {paragraphs[0]}")
        lines.append("")
        for para in paragraphs[1:]:
            lines.append(para)
            lines.append("")
    return "\n".join(lines)


def _tidy_case(text: str) -> str:
    """Capitalize turn starts and post-sentence letters.

    Whisper leaves long stretches lowercase; full repunctuation would need
    another model, but capitalizing after . ? ! and 'I' fixes the worst.
    """
    text = re.sub(r"(^|[.?!] +)([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), text)
    return re.sub(r"\bi(['\s])", r"I\1", text)


def _paragraphs(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = _sentence_re.split(text)
    paras, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > max_chars:
            paras.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current.strip():
        paras.append(current.strip())
    return paras


def _date_from_name(stem: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})", stem)
    if not m:
        return stem
    y, mo, d, h, mi = (int(g) for g in m.groups())
    date = datetime.date(y, mo, d)
    return f"{date.strftime('%-d %B %Y')}, {h:02d}:{mi:02d}"
