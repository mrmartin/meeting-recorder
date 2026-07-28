#!/bin/sh
# Build MeetingRecorder-x86_64.AppImage.
# Requires: rsync, wget or curl. Produces the AppImage in the project root.
set -eu

HERE="$(dirname "$(readlink -f "$0")")"
PROJECT="$(dirname "$HERE")"
BUILD="$PROJECT/build"
APPDIR="$BUILD/AppDir"
TOOL="$BUILD/appimagetool-x86_64.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

echo "==> Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/share/meeting-recorder" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"

rsync -a --exclude='__pycache__' \
    "$PROJECT/main.py" "$PROJECT/meeting_recorder" "$PROJECT/transcriber" \
    "$APPDIR/usr/share/meeting-recorder/"

install -m 755 "$HERE/AppRun" "$APPDIR/AppRun"
install -m 644 "$HERE/meeting-recorder.desktop" "$APPDIR/meeting-recorder.desktop"
install -m 644 "$HERE/meeting-recorder.png" "$APPDIR/meeting-recorder.png"
install -m 644 "$HERE/meeting-recorder.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/meeting-recorder.png"
# In-app copy: window icon + launcher self-integration at runtime
install -m 644 "$HERE/meeting-recorder.png" \
    "$APPDIR/usr/share/meeting-recorder/meeting-recorder.png"

if [ ! -x "$TOOL" ]; then
    echo "==> Downloading appimagetool (cached in build/)"
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$TOOL" "$TOOL_URL"
    else
        curl -sL -o "$TOOL" "$TOOL_URL"
    fi
    chmod +x "$TOOL"
fi

echo "==> Building AppImage"
cd "$PROJECT"
ARCH=x86_64 "$TOOL" "$APPDIR" "$PROJECT/MeetingRecorder-x86_64.AppImage"
echo "==> Done: $PROJECT/MeetingRecorder-x86_64.AppImage"
