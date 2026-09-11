"""CLI orchestrator for local or cloud meeting transcription, with caching."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from . import cache, config
from .protocol import Reporter


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="transcriber", description="Meeting transcription")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--engine", choices=["local", "cloud"],
                        default="local")
    parser.add_argument("--language",
                        help="two-letter language code (cloud default: en)")
    parser.add_argument("--stage", default="all",
                        choices=["all", "prepare", "asr", "render"])
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
        if args.engine == "cloud":
            if args.stage != "all":
                raise ValueError("--stage is only supported by the local engine")
            transcript = run_cloud_pipeline(
                audio_path, cfg, args.language or "en", args.force, rep)
        else:
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

    rep.stage("render", "Building transcript…")
    return render_stage(audio_path, work, cfg, tag, stereo, info.duration,
                        asr_paths, engine="local")


def run_cloud_pipeline(audio_path: Path, cfg: dict, language: str,
                       force: bool, rep: Reporter) -> Path:
    """Split by source, upload each channel, then use the normal renderer."""
    from meeting_recorder import settings
    from . import audio as audio_mod, cloud

    language = language.strip().lower()
    if not settings.valid_language(language):
        raise ValueError("Language must be a two-letter ISO 639-1 code.")
    key = settings.api_key()
    if not key:
        raise ValueError("Enter a cloud API key in transcription settings.")
    allowed, reason = cloud.endpoint_available()
    if not allowed:
        raise RuntimeError(reason)

    work = cache.work_dir(audio_path)
    info = audio_mod.probe(audio_path)
    stereo = info.is_stereo
    if stereo:
        wavs = {"Me": work / "me.16k.wav", "Them": work / "them.16k.wav"}
    else:
        wavs = {"mono": work / "mono.16k.wav"}
    if not cache.fresh(list(wavs.values()), [audio_path]):
        rep.stage("prepare", "Preparing audio…")
        if stereo:
            audio_mod.split_stereo(audio_path, wavs["Me"], wavs["Them"])
        else:
            audio_mod.to_mono_wav(audio_path, wavs["mono"])

    endpoint_tag = hashlib.sha256(cloud.ENDPOINT.encode()).hexdigest()[:10]
    tag = f"cloud.{endpoint_tag}.{language}"
    srt_paths = {name: work / f"{tag}.{name.lower()}.srt"
                 for name in wavs}
    pending_path = work / "cloud.pending.json"
    pending = _pending_cloud_run(pending_path, audio_path, language,
                                 cloud.ENDPOINT, force)

    asr_paths = {}
    for name, wav in wavs.items():
        srt_path = srt_paths[name]
        completed = name in pending.get("completed", []) if pending else False
        needs_upload = not cache.fresh([srt_path], [wav])
        if force:
            needs_upload = not completed
        label = {"Me": "your channel", "Them": "the remote channel",
                 "mono": "the recording"}[name]
        if needs_upload:
            stage_name = f"cloud_{name.lower()}" if stereo else "cloud"
            rep.stage(stage_name, f"Uploading {label}…", 0.0)
            srt, detected = cloud.upload(
                wav, language, key,
                on_progress=lambda p, sn=stage_name, lb=label: rep.stage(
                    sn, f"Uploading {lb}…", round(p, 3)))
            _write_text_atomic(srt_path, srt)
            cache.update_meta(work, f"cloud.{name}", {
                "endpoint": cloud.ENDPOINT, "language": language,
                "detected_language": detected,
            })
            if pending is not None:
                pending.setdefault("completed", []).append(name)
                cache.write_json(pending_path, pending)
        else:
            rep.stage("cloud_cache", f"Using saved cloud result for {label}…")

        speaker = name if stereo else "Speaker"
        segments = cloud.parse_srt(srt_path.read_text(), speaker)
        asr_path = work / f"asr.{name.lower()}.{tag}.json"
        cache.write_json(asr_path, segments)
        asr_paths[name] = asr_path

    rep.stage("render", "Building transcript…")
    transcript = render_stage(
        audio_path, work, cfg, tag, stereo, info.duration, asr_paths,
        engine="cloud", model="mono-speech", language=language)
    if pending is not None:
        pending_path.unlink(missing_ok=True)
    return transcript


def _pending_cloud_run(path: Path, audio_path: Path, language: str,
                       endpoint: str, force: bool) -> dict | None:
    if not force:
        return None
    signature = {
        "audio_mtime_ns": audio_path.stat().st_mtime_ns,
        "language": language,
        "endpoint": endpoint,
    }
    try:
        pending = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        pending = {}
    if any(pending.get(key) != value for key, value in signature.items()):
        pending = {**signature, "completed": []}
        cache.write_json(path, pending)
    return pending


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def render_stage(audio_path: Path, work: Path, cfg: dict, tag: str,
                 stereo: bool, duration: float, asr_paths: dict,
                 diar_path=None, rep=None, engine: str = "local",
                 model: str | None = None,
                 language: str | None = None) -> Path:
    from . import merge, render

    pause_s = cfg["merge"]["pause_split_s"]
    dropped_quality: list = []
    if stereo:
        me_raw = cache.read_json(asr_paths["Me"])
        them_raw = cache.read_json(asr_paths["Them"])
        merge.pause_punctuate(me_raw, pause_s)
        merge.pause_punctuate(them_raw, pause_s)
        me, dq1 = merge.quality_filter(me_raw, cfg)
        them, dq2 = merge.quality_filter(them_raw, cfg)
        dropped_quality = dq1 + dq2
        me, dropped_echo = merge.echo_dedup(me, them, cfg)
        cache.write_json(work / "dropped_echo.json", dropped_echo)
        segments = me + them
        speakers = ["Me", "Them"]
    else:
        mono_raw = cache.read_json(asr_paths["mono"])
        merge.pause_punctuate(mono_raw, pause_s)
        segments, dropped_quality = merge.quality_filter(mono_raw, cfg)
        for seg in segments:
            seg["speaker"] = ""
        speakers = ["(not attributed — mono recording)"]
    cache.write_json(work / "dropped_quality.json", dropped_quality)

    turns = merge.build_turns(segments, cfg)
    model = model or cfg["asr"]["model"]
    text = render.render(turns, audio_path, duration,
                         speakers, model, cfg, engine, language)
    transcript = audio_path.with_suffix("").with_name(
        audio_path.stem + ".transcript.md")
    previous_engine = None
    try:
        previous_engine = cache.read_json(work / "meta.json").get(
            "render", {}).get("engine")
    except (OSError, ValueError, TypeError):
        pass
    if engine == "cloud" and transcript.is_file() \
            and previous_engine != "cloud":
        backup = audio_path.with_name(
            audio_path.stem + ".transcript.local.md")
        backup_tmp = backup.with_suffix(backup.suffix + ".tmp")
        shutil.copyfile(transcript, backup_tmp)
        backup_tmp.replace(backup)
    tmp = transcript.with_name(transcript.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(transcript)
    cache.update_meta(work, "render", {
        "turns": len(turns), "tag": tag, "engine": engine, "model": model,
        "language": language or cfg["asr"]["language"]})
    return transcript


if __name__ == "__main__":
    sys.exit(main())
