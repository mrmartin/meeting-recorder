#!/usr/bin/env python3
"""Cloud client/settings tests; all HTTP traffic stays on localhost."""

from __future__ import annotations

import http.server
import os
import pathlib
import shutil
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


def setUpModule():
    """Keep tests away from the real settings file and its folders."""
    global _settings_dir
    _settings_dir = tempfile.TemporaryDirectory()
    settings.SETTINGS_PATH = pathlib.Path(_settings_dir.name) / "settings.json"


def tearDownModule():
    _settings_dir.cleanup()


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
        self.addCleanup(settings.SETTINGS_PATH.unlink, missing_ok=True)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            settings.save({"recordings_dir": td})
            older = root / "meeting_older.ogg"
            newer = root / "meeting_newer.ogg"
            older.write_bytes(b"a")
            newer.write_bytes(b"b")
            os.utime(older, (1000, 1000))
            os.utime(newer, (2000, 2000))
            self.assertEqual(storage.list_recordings(), [newer, older])


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(settings.SETTINGS_PATH.unlink, missing_ok=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.recordings = root / "audio"
        self.transcripts = root / "text"
        self.recordings.mkdir()
        self.transcripts.mkdir()
        self.audio = self.recordings / "meeting_2026-09-11_12-00.ogg"
        self.audio.write_bytes(b"audio")
        self.name = "meeting_2026-09-11_12-00.transcript.md"
        settings.save({"recordings_dir": str(self.recordings)})

    def _choose_transcripts(self, folder=None):
        settings.save({"recordings_dir": str(self.recordings),
                       "transcripts_dir": str(folder or self.transcripts)})

    def test_defaults_keep_everything_beside_the_recording(self):
        settings.save({})
        self.assertEqual(storage.recordings_folder(),
                         storage.DEFAULT_RECORDINGS_DIR)
        self.assertEqual(storage.transcript_path(self.audio),
                         self.recordings / self.name)
        self.assertTrue(storage.transcripts_reachable())

    def test_new_recordings_go_to_the_chosen_folder(self):
        self.assertEqual(storage.new_recording_path().parent, self.recordings)
        self.assertEqual(storage.list_recordings(), [self.audio])

    def test_transcripts_go_to_the_chosen_folder(self):
        self._choose_transcripts()
        self.assertEqual(storage.transcript_path(self.audio),
                         self.transcripts / self.name)
        self.assertEqual(storage.local_transcript_backup_path(self.audio).parent,
                         self.transcripts)
        self.assertEqual(storage.transcripts_dir(), self.transcripts)

    def test_choosing_a_folder_keeps_earlier_cloud_transcripts_done(self):
        (self.recordings / self.name).write_text(
            "- **Transcribed:** 2026-10-12 · mono-speech (cloud)\n")
        self._choose_transcripts()
        self.assertTrue(storage.has_transcript(self.audio))
        self.assertTrue(storage.has_cloud_result(self.audio))

    def test_chosen_folder_wins_over_a_copy_beside_the_recording(self):
        (self.recordings / self.name).write_text("old")
        (self.transcripts / self.name).write_text("new")
        self._choose_transcripts()
        self.assertEqual(storage.transcript_path(self.audio).read_text(), "new")

    def test_missing_transcripts_folder_is_not_reachable(self):
        self._choose_transcripts(self.transcripts / "unplugged")
        self.assertFalse(storage.transcripts_reachable())

    def test_move_transcripts_never_replaces_a_file(self):
        (self.recordings / self.name).write_text("moved")
        backup = "meeting_2026-09-11_12-00.transcript.local.md"
        (self.recordings / backup).write_text("backup")
        (self.recordings / "meeting_2.transcript.md").write_text("source")
        (self.transcripts / "meeting_2.transcript.md").write_text("existing")
        storage.move_transcripts(self.recordings, self.transcripts)
        self.assertEqual((self.transcripts / self.name).read_text(), "moved")
        self.assertEqual((self.transcripts / backup).read_text(), "backup")
        self.assertEqual(
            (self.transcripts / "meeting_2.transcript.md").read_text(),
            "existing")
        self.assertTrue((self.recordings / "meeting_2.transcript.md").exists())
        self.assertTrue(self.audio.exists())

    def test_render_writes_to_the_chosen_folder(self):
        self._choose_transcripts()
        cfg = config.load(None)
        work = cache.work_dir(self.audio)
        asr = work / "asr.json"
        cache.write_json(asr, [{"speaker": "Speaker", "start": 1.0, "end": 2.0,
                                "text": "Hello there.", "words": []}])
        transcript = render_stage(
            self.audio, work, cfg, "tag", False, 3.0, {"mono": asr},
            work / "none.json", Reporter(False), engine="cloud",
            model="mono-speech", language="en")
        self.assertEqual(transcript, self.transcripts / self.name)
        self.assertEqual(storage.transcript_engine(self.audio), "cloud")
        self.assertFalse((self.recordings / self.name).exists())


CLOUD_HEADER = "- **Transcribed:** 2026-10-12 · mono-speech (cloud)\n"
LOCAL_HEADER = "- **Transcribed:** 2026-10-12 · large-v3-turbo (local)\n"


class TranscribeAllTests(unittest.TestCase):
    """What Transcribe all leaves alone, whatever happens to the files."""

    def setUp(self):
        history = settings.SETTINGS_PATH.with_name("transcribed.json")
        history.unlink(missing_ok=True)
        self.addCleanup(history.unlink, missing_ok=True)
        self.addCleanup(settings.SETTINGS_PATH.unlink, missing_ok=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        settings.save({"recordings_dir": str(self.root)})
        self.audio = self.root / "meeting_2026-09-11_12-00.ogg"
        self.audio.write_bytes(b"audio")
        self.transcript = self.root / "meeting_2026-09-11_12-00.transcript.md"

    def _render(self, engine):
        work = cache.work_dir(self.audio)
        asr = work / "asr.json"
        cache.write_json(asr, [{"speaker": "Speaker", "start": 1.0, "end": 2.0,
                                "text": "Hello there.", "words": []}])
        render_stage(self.audio, work, config.load(None), "tag", False, 3.0,
                     {"mono": asr}, work / "none.json", Reporter(False),
                     engine=engine, model="model", language="en")

    def _lose_cache_and_transcript(self):
        shutil.rmtree(self.root / ".transcribe")
        self.transcript.unlink()

    def test_new_recording_is_picked_up(self):
        self.assertFalse(storage.already_transcribed(self.audio, cloud=True))
        self.assertFalse(storage.already_transcribed(self.audio, cloud=False))

    def test_finished_cloud_job_is_never_redone(self):
        self._render("cloud")
        self._lose_cache_and_transcript()
        self.assertTrue(storage.already_transcribed(self.audio, cloud=True))
        self.assertTrue(storage.already_transcribed(self.audio, cloud=False))

    def test_transcripts_from_before_the_record_are_remembered(self):
        self.transcript.write_text(CLOUD_HEADER)
        storage.remember_existing()
        self.transcript.unlink()
        self.assertTrue(storage.already_transcribed(self.audio, cloud=True))

    def test_local_transcript_still_gets_its_cloud_run(self):
        self._render("local")
        self.assertFalse(storage.already_transcribed(self.audio, cloud=True))
        self.assertTrue(storage.already_transcribed(self.audio, cloud=False))

    def test_cloud_transcript_is_not_redone_locally(self):
        self.transcript.write_text(CLOUD_HEADER)
        self.assertTrue(storage.already_transcribed(self.audio, cloud=False))

    def test_new_recording_reusing_a_name_is_picked_up(self):
        self._render("cloud")
        self._lose_cache_and_transcript()
        self.audio.write_bytes(b"a different meeting")
        self.assertFalse(storage.already_transcribed(self.audio, cloud=True))

    def test_manual_retranscription_stays_possible(self):
        self._render("cloud")
        self._lose_cache_and_transcript()
        self.assertFalse(storage.has_cloud_result(self.audio))

    def test_unreadable_record_falls_back_to_the_files(self):
        self.transcript.write_text(LOCAL_HEADER)
        settings.SETTINGS_PATH.with_name("transcribed.json").write_text("{]")
        self.assertTrue(storage.already_transcribed(self.audio, cloud=False))
        self.assertFalse(storage.already_transcribed(self.audio, cloud=True))


if __name__ == "__main__":
    unittest.main()
