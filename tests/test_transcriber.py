#!/usr/bin/env python3
"""Unit tests for the transcriber logic (no audio hardware needed).

Run: python3 tests/test_transcriber.py   (needs: pip install rapidfuzz)
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from transcriber import config, merge, render  # noqa: E402


def seg(speaker, start, end, text, **kw):
    base = {"speaker": speaker, "start": start, "end": end, "text": text,
            "avg_logprob": -0.3, "no_speech_prob": 0.1,
            "compression_ratio": 1.2, "words": []}
    base.update(kw)
    return base


def test_config_overrides():
    cfg = config.load(["merge.turn_gap_s=2.5", "echo.set_ratio=95",
                       "merge.drop_backchannels=true"])
    assert cfg["merge"]["turn_gap_s"] == 2.5
    assert cfg["echo"]["set_ratio"] == 95
    assert cfg["merge"]["drop_backchannels"] is True
    assert cfg["asr"]["model"] == "large-v3-turbo"  # default untouched


def test_quality_filter():
    cfg = config.load()
    segments = [
        seg("Me", 0, 2, "Hello there"),
        seg("Me", 3, 4, "Thanks for watching!", avg_logprob=-0.9),
        seg("Me", 5, 6, "la la la", no_speech_prob=0.9,
            compression_ratio=3.0),
        seg("Me", 7, 8, "Same line"), seg("Me", 8, 9, "Same line"),
        seg("Me", 9, 10, "Same line"),  # 3rd identical -> dropped
    ]
    kept, dropped = merge.quality_filter(segments, cfg)
    assert [k["text"] for k in kept] == ["Hello there", "Same line",
                                        "Same line"]
    assert sorted(d["reason"] for d in dropped) == [
        "blacklist", "compression", "repeat"]


def test_echo_dedup():
    cfg = config.load()
    them = [seg("Them", 10, 14, "We should finalize the rollout "
                                "schedule by Friday."),
            seg("Them", 15, 18, "The budget review is next Tuesday.")]
    me = [
        seg("Me", 10.1, 14.1, "We should finalize the rollout schedule "
                              "by Friday"),   # verbatim echo
        seg("Me", 15.2, 16.5, "budget review"),  # degraded fragment echo
        seg("Me", 16.0, 16.4, "Yeah."),           # genuine backchannel
        seg("Me", 20, 23, "Perfect, I will prepare the numbers."),
    ]
    kept, dropped = merge.echo_dedup(me, them, cfg)
    assert [k["text"] for k in kept] == [
        "Yeah.", "Perfect, I will prepare the numbers."]
    assert {d["reason"] for d in dropped} == {"echo_full", "echo_subset"}


def test_pause_punctuate():
    cfg = config.load()
    words = [
        {"start": 0.0, "end": 0.3, "word": " so that works"},
        {"start": 1.2, "end": 1.5, "word": " next topic"},   # 0.9s gap
        {"start": 1.6, "end": 1.7, "word": " the"},          # function word
        {"start": 2.9, "end": 3.0, "word": " plan"},         # gap after 'the'
    ]
    segments = [seg("Me", 0, 3, "so that works next topic the plan",
                    words=words)]
    merge.pause_punctuate(segments, cfg["merge"]["pause_split_s"])
    assert segments[0]["text"] == "so that works. next topic the plan"


def test_turns_and_fragments():
    cfg = config.load()
    segments = [
        seg("Me", 0, 2, "Hi."), seg("Me", 3, 5, "How are you?"),
        seg("Them", 5.5, 8, "Good thanks."),
        seg("Them", 15, 16, "But"),       # isolated fragment -> dropped
        seg("Me", 20, 22, "New topic."),
    ]
    turns = merge.build_turns(segments, cfg)
    assert [(t["speaker"], " ".join(t["texts"])) for t in turns] == [
        ("Me", "Hi. How are you?"), ("Them", "Good thanks."),
        ("Me", "New topic.")]


def test_render():
    cfg = config.load()
    turns = merge.build_turns(
        [seg("Me", 0, 2, "hello simon. i think so"),
         seg("Them", 10, 12, "Great.")], cfg)
    md = render.render(turns, pathlib.Path("meeting_2026-07-28_15-01.ogg"),
                       125, ["Me", "Them"], "large-v3-turbo", cfg)
    assert "# Meeting transcript — 28 July 2026, 15:01" in md
    assert "**[00:00:00] Me:** Hello simon. I think so" in md
    assert "**[00:00:10] Them:** Great." in md
    assert "2 min 5 s" in md


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
