"""Merging: quality filters, echo dedup, and turn building.

All functions operate on cached ASR segment JSON, so tuning any threshold
only requires re-running the (sub-second) render stage.
"""

from __future__ import annotations

import re

from .config import HALLUCINATION_BLACKLIST

BACKCHANNEL_WORDS = {"yeah", "yep", "right", "okay", "ok", "mm-hmm",
                     "uh-huh", "sure", "exactly", "hmm", "mhm"}

_norm_re = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str) -> str:
    return _norm_re.sub("", text.lower().replace("'", "")).strip()


# ---------- quality filters (hallucination guards) ----------

def quality_filter(segments: list[dict], cfg: dict) -> tuple[list, list]:
    """Returns (kept, dropped) — dropped entries carry a 'reason'."""
    q = cfg["quality"]
    kept, dropped = [], []
    prev_norm, repeat_run = None, 0
    for seg in segments:
        norm = normalize(seg["text"])
        reason = None
        if not norm:
            reason = "empty"
        elif (seg["no_speech_prob"] > q["no_speech_prob"]
              and seg["avg_logprob"] < q["avg_logprob"]):
            reason = "no_speech"
        elif seg["compression_ratio"] > q["compression_ratio"]:
            reason = "compression"
        elif (any(b in norm for b in HALLUCINATION_BLACKLIST)
              and seg["avg_logprob"] < q["blacklist_logprob"]):
            reason = "blacklist"
        else:
            if norm == prev_norm:
                repeat_run += 1
                if repeat_run >= 2:  # 3rd+ identical consecutive segment
                    reason = "repeat"
            else:
                repeat_run = 0
            prev_norm = norm
        if reason:
            dropped.append({**seg, "reason": reason})
        else:
            kept.append(seg)
    return kept, dropped


# ---------- echo dedup (mic segments that are speaker bleed) ----------

def echo_dedup(me_segs: list[dict], them_segs: list[dict],
               cfg: dict) -> tuple[list, list]:
    """Drop Me segments that duplicate time-overlapping Them speech.

    Returns (kept_me, dropped_me) — dropped entries carry match details.
    """
    from rapidfuzz import fuzz

    e = cfg["echo"]
    kept, dropped = [], []
    for m in me_segs:
        m_norm = normalize(m["text"])
        words = m_norm.split()
        m_dur = max(m["end"] - m["start"], 0.01)
        lo, hi = m["start"] - e["pad_s"], m["end"] + e["pad_s"]
        overlapping = [
            t for t in them_segs
            if min(hi, t["end"]) - max(lo, t["start"]) > 0
        ]
        # Genuine simultaneous "yeah"/"okay" must survive even when the
        # remote side says the same word at the same moment.
        backchannel = bool(words) and all(w in BACKCHANNEL_WORDS
                                          for w in words)
        verdict = None
        for t in overlapping:
            overlap = (min(hi, t["end"]) - max(lo, t["start"])) / m_dur
            if overlap < e["overlap_min"]:
                continue
            t_norm = normalize(t["text"])
            full = fuzz.token_sort_ratio(m_norm, t_norm)
            if backchannel:
                if full == 100 and overlap >= 0.9:
                    verdict = ("short_exact", full, t)
                    break
                continue
            if full >= e["full_ratio"]:
                verdict = ("full", full, t)
                break
        if verdict is None and not backchannel and overlapping:
            # Room echo is acoustically degraded, so Whisper renders it as
            # fragments/mishearings that a straight ratio misses. If the
            # mic segment's words are (almost) a subset of what the remote
            # side said around the same time, it is echo.
            concat = " ".join(normalize(t["text"]) for t in overlapping)
            subset = fuzz.token_set_ratio(m_norm, concat)
            if subset >= e["set_ratio"]:
                verdict = ("subset", subset, None)
            elif len(words) >= e["partial_min_words"]:
                partial = fuzz.partial_ratio(m_norm, concat)
                if partial >= e["partial_ratio"]:
                    verdict = ("partial", partial, None)
        if verdict:
            kind, score, t = verdict
            dropped.append({**m, "reason": f"echo_{kind}", "score": score,
                            "matched": t["text"] if t else None})
        else:
            kept.append(m)
    return kept, dropped


# ---------- turn building ----------

def build_turns(segments: list[dict], cfg: dict) -> list[dict]:
    """Group time-sorted segments into speaker turns."""
    m = cfg["merge"]
    turns = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        turn = turns[-1] if turns else None
        if (turn and turn["speaker"] == seg["speaker"]
                and seg["start"] - turn["end"] < m["turn_gap_s"]):
            turn["texts"].append(seg["text"])
            turn["end"] = max(turn["end"], seg["end"])
        else:
            turns.append({"speaker": seg["speaker"], "start": seg["start"],
                          "end": seg["end"], "texts": [seg["text"]]})
    if m["drop_backchannels"]:
        turns = _drop_backchannels(turns)
    return turns


def _drop_backchannels(turns: list[dict]) -> list[dict]:
    """Drop tiny interjections fully inside another speaker's turn."""
    out = []
    for i, turn in enumerate(turns):
        words = normalize(" ".join(turn["texts"])).split()
        if len(words) <= 2 and all(w in BACKCHANNEL_WORDS for w in words):
            prev = turns[i - 1] if i > 0 else None
            nxt = turns[i + 1] if i + 1 < len(turns) else None
            surrounded = (prev and nxt
                          and prev["speaker"] == nxt["speaker"]
                          and prev["speaker"] != turn["speaker"])
            if surrounded:
                continue
        out.append(turn)
    return out
