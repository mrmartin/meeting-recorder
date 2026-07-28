#!/bin/sh
# Dev launcher: find a python3 that has PyGObject (skips conda pythons).
HERE="$(dirname "$(readlink -f "$0")")"
for PY in /usr/bin/python3 python3; do
    if "$PY" -c 'import gi' >/dev/null 2>&1; then
        exec "$PY" "$HERE/main.py" "$@"
    fi
done
echo "Meeting Recorder needs PyGObject." >&2
echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 gstreamer1.0-pulseaudio" >&2
echo "  Fedora:        sudo dnf install python3-gobject gtk3" >&2
exit 1
