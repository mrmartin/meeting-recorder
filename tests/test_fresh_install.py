#!/usr/bin/env python3
"""End-to-end check of a first-time setup on a clean system.

Installs the transcription components into a throwaway home folder, then
transcribes a short two-sided call and checks both speakers come out.

Needs ffmpeg, espeak-ng and internet.
Run: python3 tests/test_fresh_install.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ME = "Thanks for joining. Can we go through the launch checklist today?"
THEM = "Yes. The website is done, and the pricing page goes live on Thursday."


def run(cmd, **kwargs):
    result = subprocess.run([str(part) for part in cmd], capture_output=True,
                            text=True, **kwargs)
    return result.returncode, (result.stdout + result.stderr).strip()


def must(cmd, **kwargs):
    code, output = run(cmd, **kwargs)
    if code != 0:
        sys.exit(f"FAIL {cmd[0]}: {output}")
    return output


def make_call(folder):
    """A recording shaped like the app's own: Me on the left channel, Them
    on the right a few seconds later, as two channels with no declared
    layout (Opus mapping family 255)."""
    for name, text in (("me", ME), ("them", THEM)):
        must(["espeak-ng", "-s", "140", "-w", folder / f"{name}.wav", text])
    call = folder / "call.ogg"
    must(["ffmpeg", "-y", "-loglevel", "error",
          "-i", folder / "me.wav", "-i", folder / "them.wav",
          "-filter_complex",
          "[0:a]aresample=48000,apad=whole_dur=14[m];"
          "[1:a]aresample=48000,adelay=7000,apad=whole_dur=14[t];"
          "[m][t]join=inputs=2:channel_layout=stereo[a]",
          "-map", "[a]", "-c:a", "libopus", "-mapping_family", "255",
          "-b:a", "64k", call])
    return call


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("XDG_")}
        env.update(HOME=str(home), PYTHONPATH=str(ROOT))
        call = make_call(home)

        code, output = run([sys.executable, "-m", "transcriber.bootstrap"],
                           cwd=ROOT, env=env)
        if code != 0 and "no build for Python" in output:
            print(f"NOT SUPPORTED YET on this system: {output.splitlines()[-1]}")
            return
        if code != 0:
            sys.exit(f"FAIL setup: {output}")
        print("PASS setup")

        python = home / ".local/share/meeting-recorder/venv/bin/python"
        must([python, "-m", "transcriber", call, "--model", "tiny"],
             cwd=ROOT, env=env)
        transcript = (home / "call.transcript.md").read_text()
        me, them = transcript.find("Me:**"), transcript.find("Them:**")
        if me == -1 or them == -1 or me > them:
            sys.exit(f"FAIL transcript lacks Me followed by Them:\n{transcript}")
        print("PASS transcription")
        print(transcript[transcript.index("---"):].strip())


if __name__ == "__main__":
    main()
