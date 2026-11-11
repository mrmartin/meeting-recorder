"""Small, dependency-free application settings store.

The cloud API key is deliberately kept out of command lines and logs.  This
file is plaintext, but both its directory and the file itself are restricted
to the current user.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


SETTINGS_PATH = _config_home() / "meeting-recorder" / "settings.json"
DEFAULTS = {
    "cloud_enabled": False,
    "cloud_language": "en",
    "cloud_api_key": "",
    "cloud_consent": False,
    "transcribe_all": False,
    "recordings_dir": "",  # empty: ~/Recordings
    "transcripts_dir": "",  # empty: beside each recording
}


def valid_language(value: str) -> bool:
    """The service accepts two-letter ISO 639-1 language identifiers."""
    return bool(re.fullmatch(r"[A-Za-z]{2}", value.strip()))


def load() -> dict:
    values = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        if isinstance(data, dict):
            values.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError, TypeError):
        pass
    language = str(values.get("cloud_language", "en")).lower()
    values["cloud_language"] = language if valid_language(language) else "en"
    values["cloud_api_key"] = str(values.get("cloud_api_key", "")).strip()
    values["cloud_enabled"] = bool(values.get("cloud_enabled"))
    values["cloud_consent"] = bool(values.get("cloud_consent"))
    values["transcribe_all"] = bool(values.get("transcribe_all"))
    values["recordings_dir"] = str(values.get("recordings_dir") or "")
    values["transcripts_dir"] = str(values.get("transcripts_dir") or "")
    return values


def save(values: dict) -> None:
    """Atomically save known settings with user-only permissions."""
    data = {key: values.get(key, default) for key, default in DEFAULTS.items()}
    language = str(data["cloud_language"]).strip().lower()
    if not valid_language(language):
        raise ValueError("Language must be a two-letter ISO 639-1 code.")
    data["cloud_language"] = language
    data["cloud_api_key"] = str(data["cloud_api_key"]).strip()

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(SETTINGS_PATH.parent, 0o700)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        tmp.replace(SETTINGS_PATH)
        os.chmod(SETTINGS_PATH, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def api_key() -> str:
    """Load the key, allowing a file-backed development/test override."""
    override = os.environ.get("MEETING_REC_CLOUD_API_KEY_FILE")
    if override:
        try:
            return Path(override).read_text().strip()
        except OSError as exc:
            raise RuntimeError(f"Could not read cloud API key file: {exc}") \
                from exc
    return load()["cloud_api_key"]
