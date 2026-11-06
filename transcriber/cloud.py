"""Streaming client and SRT parser for the mono-speech cloud endpoint."""

from __future__ import annotations

import http.client
import os
import re
import secrets
import ssl
from pathlib import Path
from urllib.parse import urlsplit

ENDPOINT = "https://martintech.co.uk/speech_to_text/mono-speech"
INSECURE_OPT_IN = "MEETING_REC_ALLOW_INSECURE_CLOUD"
CHUNK_SIZE = 1024 * 1024
# Long channels upload in parts: ~115 MB/hour keeps each one well below the
# server's 500 MB request limit and its gateway timeouts.
PART_MAX_S = 60 * 60
PART_SEARCH_S = 2 * 60


class CloudError(RuntimeError):
    def __init__(self, message: str, status: int | None = None,
                 retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def endpoint_available() -> tuple[bool, str]:
    parsed = urlsplit(ENDPOINT)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, "The cloud transcription endpoint is invalid."
    if parsed.scheme == "http" and os.environ.get(INSECURE_OPT_IN) != "1":
        return False, (
            "Cloud transcription is not available in this build because "
            "the configured service does not use HTTPS."
        )
    return True, ""


def upload(audio_path: Path, language: str, api_key: str,
           on_progress=None) -> tuple[str, str]:
    """Upload one mono audio file and return (SRT, detected language)."""
    allowed, reason = endpoint_available()
    if not allowed:
        raise CloudError(reason)
    if not api_key or "\r" in api_key or "\n" in api_key:
        raise CloudError("A valid cloud API key is required.")

    parsed = urlsplit(ENDPOINT)
    boundary = "----MeetingRecorder" + secrets.token_hex(16)
    safe_name = audio_path.name.replace('"', "_").replace("\r", "_").replace(
        "\n", "_")
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    suffix = (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"{language.lower()}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    file_size = audio_path.stat().st_size
    content_length = len(prefix) + file_size + len(suffix)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    conn_cls = (http.client.HTTPSConnection if parsed.scheme == "https"
                else http.client.HTTPConnection)
    kwargs = {"timeout": 4 * 60 * 60}
    if parsed.scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    conn = conn_cls(parsed.hostname, parsed.port, **kwargs)
    try:
        conn.putrequest("POST", path)
        conn.putheader("X-API-Key", api_key)
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(content_length))
        conn.endheaders()
        conn.send(prefix)
        sent = 0
        with open(audio_path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                if on_progress:
                    on_progress(sent / max(file_size, 1))
        conn.send(suffix)
        response = conn.getresponse()
        body = response.read()
        detected = response.getheader("X-Detected-Language") or language.lower()
        if response.status != 200:
            _raise_status(response.status, body,
                          response.getheader("Retry-After"))
        try:
            return body.decode("utf-8-sig"), detected.lower()
        except UnicodeDecodeError as exc:
            raise CloudError("The cloud service returned invalid text.") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise CloudError(f"Could not reach the cloud service: {exc}") from exc
    finally:
        conn.close()


def _raise_status(status: int, body: bytes, retry_after: str | None) -> None:
    detail = body.decode("utf-8", errors="replace").strip()
    if len(detail) > 500:
        detail = detail[:500] + "…"
    if status == 400:
        msg = "The service rejected the language or audio file."
    elif status == 401:
        msg = "The cloud API key was rejected."
    elif status == 503:
        wait = f" Try again after {retry_after} seconds." if retry_after else ""
        msg = "The cloud service is busy or unavailable." + wait
    elif status == 500:
        msg = "The cloud transcription pipeline failed."
    elif 300 <= status < 400:
        msg = "The cloud service attempted an unsafe redirect; it was not followed."
    else:
        msg = f"The cloud service returned HTTP {status}."
    if detail and status not in (401,):
        msg += f"\n\n{detail}"
    raise CloudError(msg, status=status, retry_after=retry_after)


_TIME_RE = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$"
)


def parse_srt(text: str, speaker: str, offset: float = 0.0) -> list[dict]:
    """Convert ordinary SRT cues into the segment shape used by merging.

    offset moves the cues of one uploaded part onto the recording's timeline.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []  # a silent channel legitimately has no cues
    segments = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_index = next((i for i, line in enumerate(lines)
                           if "-->" in line), None)
        if time_index is None:
            continue
        match = _TIME_RE.match(lines[time_index])
        if not match:
            raise CloudError("The cloud service returned malformed SRT timestamps.")
        cue_text = " ".join(lines[time_index + 1:]).strip()
        if not cue_text:
            continue
        nums = [int(value) for value in match.groups()]
        start = _seconds(*nums[:4])
        end = _seconds(*nums[4:])
        if end < start:
            raise CloudError("The cloud service returned a backwards SRT cue.")
        segments.append({
            "speaker": speaker,
            "start": round(start + offset, 3),
            "end": round(end + offset, 3),
            "text": cue_text,
            "words": [],
        })
    if not segments:
        raise CloudError("The cloud service returned no readable SRT cues.")
    return segments


def _seconds(hours: int, minutes: int, seconds: int, millis: int) -> float:
    return hours * 3600 + minutes * 60 + seconds + millis / 1000
