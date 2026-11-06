#!/usr/bin/env python3
"""Cloud client/settings tests; all HTTP traffic stays on localhost."""

from __future__ import annotations

import http.server
import os
import pathlib
import stat
import struct
import sys
import tempfile
import threading
import unittest
import wave
from array import array
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from meeting_recorder import settings, storage  # noqa: E402
from transcriber import audio as audio_mod, cache, cloud, config  # noqa: E402
from transcriber.__main__ import render_stage, run_cloud_pipeline  # noqa: E402
from transcriber.protocol import Reporter  # noqa: E402


SRT = "1\n00:00:01,250 --> 00:00:03,500\nHello from cloud.\n"


class Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    retry_after = None
    request_body = b""
    api_key = None
    requests = 0
    fail_request = None

    def do_POST(self):
        type(self).requests += 1
        type(self).api_key = self.headers.get("X-API-Key")
        type(self).request_body = self.rfile.read(
            int(self.headers["Content-Length"]))
        if type(self).requests == type(self).fail_request:
            type(self).status = 503
        self.send_response(type(self).status)
        if type(self).retry_after:
            self.send_header("Retry-After", type(self).retry_after)
        self.send_header("X-Detected-Language", "EN")
        self.end_headers()
        self.wfile.write(SRT.encode() if type(self).status == 200
                         else b'{"error":"busy"}')

    def log_message(self, _format, *_args):
        pass


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.old_endpoint = cloud.ENDPOINT
        self.old_opt_in = os.environ.get(cloud.INSECURE_OPT_IN)
        os.environ[cloud.INSECURE_OPT_IN] = "1"
        Handler.status = 200
        Handler.retry_after = None
        Handler.request_body = b""
        Handler.api_key = None
        Handler.requests = 0
        Handler.fail_request = None
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cloud.ENDPOINT = (
            f"http://127.0.0.1:{self.server.server_port}/mono-speech")
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        cloud.ENDPOINT = self.old_endpoint
        if self.old_opt_in is None:
            os.environ.pop(cloud.INSECURE_OPT_IN, None)
        else:
            os.environ[cloud.INSECURE_OPT_IN] = self.old_opt_in

    def test_streaming_multipart_and_parse(self):
        with tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "channel.wav"
            audio.write_bytes(b"audio bytes")
            text, detected = cloud.upload(audio, "en", "secret")
        self.assertEqual(detected, "en")
        self.assertEqual(Handler.api_key, "secret")
        self.assertIn(b'name="file"', Handler.request_body)
        self.assertIn(b"audio bytes", Handler.request_body)
        self.assertIn(b'name="language"', Handler.request_body)
        self.assertIn(b"\r\n\r\nen\r\n", Handler.request_body)
        segments = cloud.parse_srt(text, "Me")
        self.assertEqual(segments[0]["speaker"], "Me")
        self.assertEqual(segments[0]["start"], 1.25)
        self.assertEqual(cloud.parse_srt("\n", "Them"), [])

    def test_busy_is_not_retried(self):
        Handler.status = 503
        Handler.retry_after = "120"
        with tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "channel.wav"
            audio.write_bytes(b"audio bytes")
            with self.assertRaises(cloud.CloudError) as caught:
                cloud.upload(audio, "en", "secret")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.retry_after, "120")
        self.assertIn("120 seconds", str(caught.exception))


    def _recording(self, root):
        """A 12 s mono recording, with its prepared WAV already cached."""
        audio = root / "meeting_2026-09-11_12-00.ogg"
        audio.write_bytes(b"audio")
        write_wav(cache.work_dir(audio) / "mono.16k.wav", 12.0, [(3.5, 4.5)])
        return audio

    def _transcribe(self, audio):
        with mock.patch.object(audio_mod, "probe",
                               return_value=audio_mod.AudioInfo(1, 12.0)), \
                mock.patch.object(settings, "api_key", return_value="secret"):
            return run_cloud_pipeline(audio, config.load(None), "en", False,
                                      Reporter(False))

    def test_long_recording_uploads_in_parts_on_one_timeline(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(cloud, "PART_MAX_S", 6), \
                mock.patch.object(cloud, "PART_SEARCH_S", 2):
            audio = self._recording(pathlib.Path(td))
            self._transcribe(audio)
            self.assertEqual(Handler.requests, 2)
            work = cache.work_dir(audio)
            asr = cache.read_json(next(work.glob("asr.mono.*.json")))
            self.assertEqual([s["start"] for s in asr], [1.25, 5.5])
            self.assertEqual(list(work.glob("*.part.wav")), [])
            self.assertTrue(storage.has_cloud_result(audio))

    def test_interrupted_upload_resumes_from_next_part(self):
        Handler.fail_request = 2
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(cloud, "PART_MAX_S", 6), \
                mock.patch.object(cloud, "PART_SEARCH_S", 2):
            audio = self._recording(pathlib.Path(td))
            with self.assertRaises(cloud.CloudError):
                self._transcribe(audio)
            self.assertFalse(storage.has_cloud_result(audio))

            Handler.status = 200
            self._transcribe(audio)
            self.assertEqual(Handler.requests, 3)
            self.assertTrue(storage.has_cloud_result(audio))


def write_wav(path, duration, quiet_spans=()):
    """16 kHz tone with silent spans, each sample distinct enough to compare."""
    rate = 16000
    samples = array("h", (8000 + i % 1000 if i % 2 else -8000 - i % 1000
                          for i in range(int(duration * rate))))
    for start, end in quiet_spans:
        for i in range(int(start * rate), int(end * rate)):
            samples[i] = 0
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


def make_extensible(path):
    """Rewrite a WAV the way ffmpeg writes a split stereo channel: an
    extensible fmt chunk, with an odd-sized metadata chunk before the audio."""
    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        pcm = reader.readframes(reader.getnframes())
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, rate, rate * 2, 2, 16, 22, 16,
                      1) + bytes(16)
    body = (b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
            + b"LIST" + struct.pack("<I", 5) + b"abcde\0"
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return pcm


class PartPlanningTests(unittest.TestCase):
    def test_reads_extensible_channel_wavs(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            wav = root / "me.16k.wav"
            write_wav(wav, 12.0, [(4.5, 5.5)])
            pcm = make_extensible(wav)
            parts = audio_mod.plan_parts(wav, 6, 2)
            self.assertEqual(len(parts), 2)
            self.assertTrue(4.5 <= parts[0][1] <= 5.5, parts)
            self.assertEqual(parts[1][1], 12.0)
            joined = b""
            for i, (start, end) in enumerate(parts):
                part = root / f"{i}.wav"
                audio_mod.extract_part(wav, start, end, part)
                with wave.open(str(part), "rb") as reader:
                    joined += reader.readframes(reader.getnframes())
            self.assertEqual(joined, pcm)

    def test_short_recording_stays_whole(self):
        with tempfile.TemporaryDirectory() as td:
            wav = pathlib.Path(td) / "a.wav"
            write_wav(wav, 4.0)
            self.assertEqual(audio_mod.plan_parts(wav, 6, 2), [(0.0, 4.0)])

    def test_cut_lands_in_the_pause(self):
        with tempfile.TemporaryDirectory() as td:
            wav = pathlib.Path(td) / "a.wav"
            write_wav(wav, 12.0, [(4.5, 5.5)])
            (_, cut), (start, end) = audio_mod.plan_parts(wav, 6, 2)
            self.assertEqual(cut, start)
            self.assertEqual(end, 12.0)
            self.assertTrue(4.5 <= cut <= 5.5, cut)

    def test_parts_rejoin_sample_exact(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            wav = root / "a.wav"
            write_wav(wav, 12.0, [(3.0, 3.8), (8.2, 9.0)])
            joined = b""
            for i, (start, end) in enumerate(audio_mod.plan_parts(wav, 4, 1)):
                part = root / f"{i}.wav"
                audio_mod.extract_part(wav, start, end, part)
                with wave.open(str(part), "rb") as reader:
                    joined += reader.readframes(reader.getnframes())
            with wave.open(str(wav), "rb") as reader:
                self.assertEqual(joined, reader.readframes(reader.getnframes()))


class StorageTests(unittest.TestCase):
    def test_settings_are_mode_0600(self):
        old_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                settings.SETTINGS_PATH = pathlib.Path(td) / "cfg" / "settings.json"
                values = dict(settings.DEFAULTS, cloud_api_key="not-logged",
                              cloud_language="DE", transcribe_all=True)
                settings.save(values)
                loaded = settings.load()
                self.assertEqual(loaded["cloud_language"], "de")
                self.assertEqual(loaded["cloud_api_key"], "not-logged")
                self.assertTrue(loaded["transcribe_all"])
                mode = stat.S_IMODE(settings.SETTINGS_PATH.stat().st_mode)
                self.assertEqual(mode, 0o600)
        finally:
            settings.SETTINGS_PATH = old_path

    def test_cloud_render_backs_up_local_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            audio = root / "meeting_2026-09-11_10-00.ogg"
            audio.write_bytes(b"placeholder")
            transcript = root / "meeting_2026-09-11_10-00.transcript.md"
            transcript.write_text("local result")
            work = root / ".transcribe" / audio.stem
            work.mkdir(parents=True)
            cache.write_json(work / "meta.json", {
                "render": {"engine": "local"}})
            me = work / "asr.me.cloud.json"
            them = work / "asr.them.cloud.json"
            cache.write_json(me, [{"speaker": "Me", "start": 1,
                                   "end": 2, "text": "Hello", "words": []}])
            cache.write_json(them, [{"speaker": "Them", "start": 3,
                                     "end": 4, "text": "Hi", "words": []}])
            render_stage(audio, work, config.load(), "cloud", True, 5,
                         {"Me": me, "Them": them}, work / "none.json",
                         Reporter(False), engine="cloud",
                         model="mono-speech", language="en")
            backup = root / "meeting_2026-09-11_10-00.transcript.local.md"
            self.assertEqual(backup.read_text(), "local result")
            self.assertIn("mono-speech (cloud)", transcript.read_text())
            self.assertEqual(cache.read_json(work / "meta.json")["render"]
                             ["engine"], "cloud")

    def test_engine_badges_and_cloud_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            audio = root / "meeting_2026-09-11_12-00.ogg"
            audio.write_bytes(b"audio")
            storage.transcript_path(audio).write_text("transcript")
            work = root / ".transcribe" / audio.stem
            work.mkdir(parents=True)

            self.assertEqual(storage.transcript_engine(audio), "local")
            self.assertFalse(storage.has_cloud_result(audio))
            cache.write_json(work / "meta.json", {
                "cloud.Me": {"language": "en"},
                "cloud.Them": {"language": "en"},
                "render": {"engine": "cloud"},
            })
            self.assertEqual(storage.transcript_engine(audio), "cloud")
            self.assertTrue(storage.has_cloud_result(audio))

            # A later local render changes the active badge, but cloud batch
            # mode must still remember not to pay for this recording again.
            cache.write_json(work / "meta.json", {
                "cloud.Me": {"language": "en"},
                "cloud.Them": {"language": "en"},
                "render": {"engine": "local"},
            })
            self.assertEqual(storage.transcript_engine(audio), "local")
            self.assertTrue(storage.has_cloud_result(audio))

    def test_cloud_history_survives_purged_cache(self):
        with tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "meeting_2026-09-11_12-00.ogg"
            audio.write_bytes(b"audio")
            storage.transcript_path(audio).write_text(
                "# Meeting transcript\n\n"
                "- **Transcribed:** 2026-10-12 · mono-speech (cloud)"
                " · language: en\n\n---\n")
            self.assertEqual(storage.transcript_engine(audio), "cloud")
            self.assertTrue(storage.has_cloud_result(audio))

            storage.transcript_path(audio).write_text(
                "- **Transcribed:** 2026-10-12 · large-v3-turbo (local)\n")
            self.assertEqual(storage.transcript_engine(audio), "local")
            self.assertFalse(storage.has_cloud_result(audio))

    def test_recording_order_is_newest_first(self):
        old_dir = storage.RECORDINGS_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                storage.RECORDINGS_DIR = pathlib.Path(td)
                older = storage.RECORDINGS_DIR / "meeting_older.ogg"
                newer = storage.RECORDINGS_DIR / "meeting_newer.ogg"
                older.write_bytes(b"old")
                newer.write_bytes(b"new")
                os.utime(older, ns=(1_000_000_000, 1_000_000_000))
                os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
                self.assertEqual(storage.list_recordings(), [newer, older])
        finally:
            storage.RECORDINGS_DIR = old_dir


if __name__ == "__main__":
    unittest.main()
