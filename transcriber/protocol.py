"""Progress reporting: JSON-lines on stdout (app mode) or human text."""

from __future__ import annotations

import json
import sys


class Reporter:
    def __init__(self, json_mode: bool):
        self.json_mode = json_mode

    def stage(self, stage: str, message: str, progress: float | None = None):
        if self.json_mode:
            self._emit({"event": "stage", "stage": stage,
                        "message": message, "progress": progress})
        else:
            pct = f" [{progress * 100:.0f}%]" if progress is not None else ""
            print(f"==> {message}{pct}", file=sys.stderr)

    def done(self, transcript_path: str):
        if self.json_mode:
            self._emit({"event": "done", "transcript": transcript_path})
        else:
            print(f"==> Transcript: {transcript_path}", file=sys.stderr)

    def error(self, message: str):
        if self.json_mode:
            self._emit({"event": "error", "message": message})
        else:
            print(f"ERROR: {message}", file=sys.stderr)

    @staticmethod
    def _emit(obj: dict):
        print(json.dumps(obj), flush=True)
