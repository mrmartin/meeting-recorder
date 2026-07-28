"""CLI orchestrator: prepare -> asr -> [diarize] -> render, with caching.

Stereo files (L=mic/Me, R=system/Them) get exact source attribution plus
echo dedup; legacy mono files get pyannote Speaker 1/2 diarization.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import cache, config
from .protocol import Reporter


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="transcriber", description="Local meeting transcription")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--stage", default="all",
                        choices=["all", "prepare", "asr", "diarize", "render"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--model", help="override asr.model")
    parser.add_argument("--tag", default=None,
                        help="cache tag for ASR results (default: model name)")
    parser.add_argument("--set", dest="overrides", action="append",
                        metavar="KEY=VALUE", default=[])
    parser.add_argument("--json", action="store_true",
                        help="machine-readable progress on stdout")
    args = parser.parse_args()

    rep = Reporter(args.json)
    cfg = config.load(args.overrides)
    if args.model:
        cfg["asr"]["model"] = args.model
    tag = args.tag or cfg["asr"]["model"].replace("/", "_")

    audio_path = args.audio.expanduser().resolve()
    if not audio_path.is_file():
        rep.error(f"File not found: {audio_path}")
        return 1

    try:
        transcript = run_pipeline(audio_path, cfg, tag, args.stage,
                                  args.force, rep)
    except Exception as exc:  # noqa: BLE001 - all errors go to the app
        rep.error(str(exc))
        return 1
    if transcript:
        rep.done(str(transcript))
    return 0


def run_pipeline(audio_path: Path, cfg: dict, tag: str, stage: str,
                 force: bool, rep: Reporter) -> Path | None:
    from . import audio as audio_mod

    work = cache.work_dir(audio_path)
    info = audio_mod.probe(audio_path)
    stereo = info.is_stereo
    run_all = stage == "all"

    # ---- prepare ----
    if stereo:
        wavs = {"Me": work / "me.16k.wav", "Them": work / "them.16k.wav"}
    else:
        wavs = {"mono": work / "mono.16k.wav"}
    if stage in ("prepare",) or run_all:
        if force or not cache.fresh(list(wavs.values()), [audio_path]):
            rep.stage("prepare", "Preparing audio…")
            if stereo:
                audio_mod.split_stereo(audio_path, wavs["Me"], wavs["Them"])
            else:
                audio_mod.to_mono_wav(audio_path, wavs["mono"])
        if stage == "prepare":
            return None

    # ---- asr ----
    asr_paths = {k: work / f"asr.{k.lower()}.{tag}.json" for k in wavs}
    if stage == "asr" or run_all:
        from . import asr as asr_mod

        for key, wav in wavs.items():
            out = asr_paths[key]
            if not force and cache.fresh([out], [wav]):
                continue
            label = {"Me": "your channel", "Them": "the remote channel",
                     "mono": "the recording"}[key]
            stage_name = f"asr_{key.lower()}" if stereo else "asr"
            rep.stage(stage_name, f"Transcribing {label}…", 0.0)
            segs = asr_mod.transcribe(
                wav, key if stereo else "Speaker", cfg,
                on_progress=lambda p, sn=stage_name, lb=label: rep.stage(
                    sn, f"Transcribing {lb}…", round(p, 3)),
                duration=info.duration,
            )
            cache.write_json(out, segs)
            cache.update_meta(work, f"asr.{key}", {
                "tag": tag, "model": cfg["asr"]["model"], "count": len(segs)})
        if stage == "asr":
            return None

    # ---- diarize (mono only) ----
    diar_path = work / "diarization.json"
    if not stereo and (stage == "diarize" or run_all):
        if force or not cache.fresh([diar_path], [wavs["mono"]]):
            from . import diarize as diar_mod

            rep.stage("diarize",
                      "Identifying speakers (takes a while, one-time)…")
            turns = diar_mod.diarize(wavs["mono"], num_speakers=2)
            cache.write_json(diar_path, turns)
        if stage == "diarize":
            return None

    # ---- render ----
    rep.stage("render", "Building transcript…")
    return render_stage(audio_path, work, cfg, tag, stereo, info.duration,
                        asr_paths, diar_path)


def render_stage(audio_path: Path, work: Path, cfg: dict, tag: str,
                 stereo: bool, duration: float, asr_paths: dict,
                 diar_path: Path) -> Path:
    from . import merge, render

    dropped_quality: list = []
    if stereo:
        me, dq1 = merge.quality_filter(cache.read_json(asr_paths["Me"]), cfg)
        them, dq2 = merge.quality_filter(
            cache.read_json(asr_paths["Them"]), cfg)
        dropped_quality = dq1 + dq2
        me, dropped_echo = merge.echo_dedup(me, them, cfg)
        cache.write_json(work / "dropped_echo.json", dropped_echo)
        segments = me + them
        speakers = ["Me", "Them"]
    else:
        from . import diarize as diar_mod

        raw, dropped_quality = merge.quality_filter(
            cache.read_json(asr_paths["mono"]), cfg)
        diar_turns = cache.read_json(diar_path)
        segments = diar_mod.assign_speakers(raw, diar_turns)
        speakers = sorted({s["speaker"] for s in segments}) or ["Speaker 1"]
        speakers = [f"{', '.join(speakers)} (auto-detected)"]
    cache.write_json(work / "dropped_quality.json", dropped_quality)

    turns = merge.build_turns(segments, cfg)
    text = render.render(turns, audio_path, duration,
                         speakers, cfg["asr"]["model"], cfg)
    transcript = audio_path.with_suffix("").with_name(
        audio_path.stem + ".transcript.md")
    transcript.write_text(text)
    cache.update_meta(work, "render", {"turns": len(turns), "tag": tag})
    return transcript


if __name__ == "__main__":
    sys.exit(main())
