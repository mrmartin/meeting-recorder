# Meeting Recorder

A tiny Linux app that records **both sides of a meeting** — everything your
computer plays (remote participants) and everything your microphone hears —
into a single audio file, and can **transcribe it locally** with clear
"Me:" / "Them:" attribution.

- One button: **Record / Stop**, with elapsed timer and live level meters
- Output: `~/Recordings/meeting_YYYY-MM-DD_HH-MM.ogg`
  (Ogg/Opus stereo, left = your mic, right = system audio, ~29 MB per hour)
- **Transcribe** button per recording: local Whisper (large-v3-turbo via
  faster-whisper), speaker-attributed Markdown minutes, no cloud
- Echo-aware: your mic hearing the speakers doesn't duplicate "Them" lines
  into your side of the transcript
- Works on PulseAudio **and** PipeWire systems
- No account, no cloud, no configuration

## Install (any Linux)

Download `MeetingRecorder-x86_64.AppImage`, make it executable, run it:

```sh
chmod +x MeetingRecorder-x86_64.AppImage
./MeetingRecorder-x86_64.AppImage
```

Or right-click → Properties → *Allow executing as program*, then double-click.

The AppImage is <1 MB because it uses the GTK/Python stack already present on
every Linux desktop. If a very minimal system is missing pieces, the app tells
you the exact install command (e.g. Debian/Ubuntu:
`sudo apt install python3-gi gir1.2-gtk-3.0 gstreamer1.0-pulseaudio`).

## Run from source (development)

```sh
./run.sh
```

`run.sh` picks the first `python3` that has PyGObject (skipping conda/pyenv
environments that lack it).

Headless smoke test of the audio pipeline (records 5 s to `/tmp`):

```sh
/usr/bin/python3 -m meeting_recorder.recorder
```

## Build the AppImage

```sh
packaging/build-appimage.sh
```

Downloads `appimagetool` on first run (cached in `build/`), assembles the
AppDir, and produces `MeetingRecorder-x86_64.AppImage` in the project root.

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
separately with faster-whisper, echo of the speakers picked up by the mic is
removed by text-similarity dedup against the system channel, and the two
sides are merged into timestamped speaker turns. Legacy mono recordings are
attributed with pyannote speaker diarization instead (needs a free
HuggingFace token). Everything is cached under `~/Recordings/.transcribe/`,
so re-runs and setting tweaks are fast.

CLI (same engine as the button):

```sh
~/.local/share/meeting-recorder/venv/bin/python -m transcriber \
    ~/Recordings/meeting_X.ogg [--model large-v3-turbo] [--set merge.turn_gap_s=2.5]
```

```
meeting_recorder/
├── main.py                     # entry point: backend selection, launch UI
├── run.sh                      # dev launcher
├── meeting_recorder/
│   ├── app.py                  # GTK window: button, timer, levels, list
│   ├── recorder.py             # GStreamer recorder (primary backend)
│   ├── recorder_fallback.py    # parecord + ffmpeg fallback backend
│   ├── devices.py              # default sink/source discovery (pactl)
│   └── storage.py              # ~/Recordings paths and listing
└── packaging/                  # AppRun, .desktop, icon, build script
```

## Tips

- Recordings capture **whatever your system plays** — mute notifications
  during meetings.
- For transcription, feed the `.ogg` straight to Whisper or any service;
  Opus is universally supported.
