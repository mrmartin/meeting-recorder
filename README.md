# Meeting Recorder

A tiny Linux app that records **both sides of a meeting** — everything your
computer plays (remote participants) and everything your microphone hears —
mixed live into a single audio file, ready for transcription.

- One button: **Record / Stop**, with elapsed timer and live level meters
- Output: `~/Recordings/meeting_YYYY-MM-DD_HH-MM.ogg`
  (Ogg/Opus, mono, 48 kHz, speech-optimized, ~14 MB per hour)
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

A single GStreamer pipeline captures the default output's **monitor source**
(`@DEFAULT_MONITOR@` — what your speakers play) and the default **microphone**
(`@DEFAULT_SOURCE@`), mixes them with `audiomixer`, and encodes with `opusenc`
in voice mode. One pipeline clock means the two streams can't drift apart.
On stop, the app sends EOS so the Ogg file is finalized and seekable.

If the host's GStreamer lacks the pulse/opus elements, the app falls back to
two `parecord` processes mixed with `ffmpeg` at stop time (level meters are
hidden in that mode).

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
