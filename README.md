# Meeting Recorder

[![CI](https://github.com/mrmartin/meeting_speech_recorder/actions/workflows/ci.yml/badge.svg)](https://github.com/mrmartin/meeting_speech_recorder/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mrmartin/meeting_speech_recorder)](https://github.com/mrmartin/meeting_speech_recorder/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/mrmartin/meeting_speech_recorder/total)](https://github.com/mrmartin/meeting_speech_recorder/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-blue)](https://github.com/mrmartin/meeting_speech_recorder/releases/latest)
[![Privacy](https://img.shields.io/badge/transcription-100%25%20local-brightgreen)](#transcription)

**Meeting Recorder records both sides of your online meetings — your microphone and the other participants — into one audio file, and transcribes it on your own computer into readable, speaker-labelled minutes.**

## Install

1. Download **[MeetingRecorder-x86_64.AppImage](https://github.com/mrmartin/meeting_speech_recorder/releases/latest)** (under 1 MB)
2. Make it executable: right-click the file → **Properties** → enable **Execute as program**
   (or in a terminal: `chmod +x MeetingRecorder-x86_64.AppImage`)
3. Double-click it to start. The app adds itself to your applications menu, so next time just search for "Meeting Recorder".

Works on any modern Linux desktop (Ubuntu, Fedora, Mint, Arch, …) with PulseAudio or PipeWire. No root, no packages, no account. In the rare case something is missing, the app tells you the exact command to install it.

## How to use

1. **Before your meeting**, open Meeting Recorder and press **● Record**. The level bars confirm both your mic and the meeting audio are being captured, and you can watch the file grow while recording.
2. **After the meeting**, press **■ Stop**. The recording appears in the list (`~/Recordings/meeting_YYYY-MM-DD_HH-MM.ogg`) and plays in any audio player.
3. Press **Transcribe** next to a recording. The first time, the app offers a one-time download of the speech-recognition components (~3 GB, all local). When it finishes, a document icon appears — click it to read your transcript:

```markdown
**[00:12:19] Me:** Absolutely, so that's what we call indexing…

**[00:12:58] Them:** Yeah, so you'd write an MCP connector for it?
```

Everything runs on your computer. No audio or text ever leaves your machine.

---

## How it works

**Recording.** A single GStreamer pipeline captures the default output's
**monitor source** (`@DEFAULT_MONITOR@` — what your speakers play) and the
default **microphone** (`@DEFAULT_SOURCE@`), and interleaves them as the two
channels of a stereo Opus stream (left = mic, right = system). Keeping the
sources on separate channels is what makes exact Me/Them transcript
attribution possible; one pipeline clock means the channels can't drift.
On stop, the app sends EOS so the Ogg file is finalized and seekable.

If the host's GStreamer lacks the pulse/opus elements, the app falls back to
two `parecord` processes joined with `ffmpeg` at stop time (level meters are
hidden in that mode).

**Transcription.** The `transcriber/` package runs in a dedicated venv
(`~/.local/share/meeting-recorder/venv`, created on first use, ~3 GB with
models — CPU-only, no CUDA). Stereo recordings: each channel is transcribed
separately with faster-whisper (large-v3-turbo), echo of the speakers picked
up by the mic is removed by text-similarity dedup against the system channel,
and the two sides are merged into timestamped speaker turns with
pause-based sentence restoration. Legacy mono recordings are attributed with
pyannote speaker diarization instead (needs a free HuggingFace token).
Everything is cached under `~/Recordings/.transcribe/`, so re-runs and
setting tweaks are fast.

CLI (same engine as the button):

```sh
~/.local/share/meeting-recorder/venv/bin/python -m transcriber \
    ~/Recordings/meeting_X.ogg [--model large-v3-turbo] [--set merge.turn_gap_s=2.5]
```

Useful settings: `--set diarize.num_speakers=3` (mono recordings),
`--set "diarize.names=Alice, Bob, Carol"` (name the speakers, in order of
first appearance), `--set merge.pause_split_s=0` (disable pause splitting).

## Development

```sh
./run.sh                                  # run from source
python3 tests/test_transcriber.py         # unit tests (pip install rapidfuzz)
/usr/bin/python3 -m meeting_recorder.recorder   # 5s headless recording smoke test
packaging/build-appimage.sh               # build the AppImage
```

`run.sh` picks the first `python3` that has PyGObject (skipping conda/pyenv
environments that lack it). CI runs the same tests and builds the AppImage
on every push.

```
meeting_recorder/
├── main.py                     # entry point: backend selection, launch UI
├── meeting_recorder/           # GTK app: window, recorder pipelines, launcher
├── transcriber/                # ASR, diarization, echo dedup, rendering
├── tests/                      # logic tests (no audio hardware needed)
└── packaging/                  # AppRun, .desktop, icon, build script
```

## Tips

- Recordings capture **whatever your system plays** — mute notifications
  during meetings.
- The transcript is plain Markdown next to the audio file — edit, grep, or
  paste it anywhere.
- Old recordings from v1 (mixed mono) still transcribe via speaker
  diarization; new stereo recordings get exact Me/Them attribution.
