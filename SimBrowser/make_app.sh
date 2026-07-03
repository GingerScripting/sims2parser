#!/bin/bash
# Build "Sim Browser.app" from the SwiftPM package.
set -euo pipefail
cd "$(dirname "$0")"

swift build -c release

APP="../Sim Browser.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cp .build/release/SimBrowser "$APP/Contents/MacOS/SimBrowser"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Sim Browser</string>
    <key>CFBundleDisplayName</key><string>Sim Browser</string>
    <key>CFBundleExecutable</key><string>SimBrowser</string>
    <key>CFBundleIdentifier</key><string>org.macadmins.rebecca.simbrowser</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>Reads Sims 2 saves read-only.</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" 2>/dev/null || true
echo "Built: $(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"
