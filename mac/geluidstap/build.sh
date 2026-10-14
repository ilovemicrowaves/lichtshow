#!/bin/bash
# Bouwt Geluidstap.app en zet hem in ~/Applications (of in $1).
set -euo pipefail
HIER="$(cd "$(dirname "$0")" && pwd)"
DOEL="${1:-$HOME/Applications/Geluidstap.app}"
mkdir -p "$DOEL/Contents/MacOS"
cp "$HIER/Info.plist" "$DOEL/Contents/Info.plist"
swiftc -O "$HIER/main.swift" -o "$DOEL/Contents/MacOS/geluidstap"
codesign --force -s - "$DOEL"
echo "gebouwd: $DOEL"
