#!/bin/bash
# Build "Sim Browser.app" from the SwiftPM package.
set -euo pipefail
cd "$(dirname "$0")"

MIN_MACOS=13.0
BUILT="/tmp/simbrowser-universal.$$"
trap 'rm -f "$BUILT"' EXIT

# Universal by default, so the app runs on Intel as well as Apple silicon.
# ARCHS=native builds only for this machine, which is quicker while iterating.
#
# Built one slice at a time and lipo'd together rather than with `swift build
# --arch arm64 --arch x86_64`, which routes through xcbuild and so needs full
# Xcode. --triple only needs the Command Line Tools, which is all the README
# asks people to install.
if [ "${ARCHS:-universal}" = native ]; then
    swift build -c release
    BIN=".build/release/SimBrowser"
else
    for arch in arm64 x86_64; do
        swift build -c release --triple "$arch-apple-macosx$MIN_MACOS"
    done
    lipo -create -output "$BUILT" \
        ".build/arm64-apple-macosx/release/SimBrowser" \
        ".build/x86_64-apple-macosx/release/SimBrowser"
    BIN="$BUILT"
fi

APP="../Sim Browser.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$BIN" "$APP/Contents/MacOS/SimBrowser"

# The Python side rides along inside the bundle, so the app does not care where
# the repo was cloned. This is the extractor plus its full import closure —
# careers.json and wants.json are read relative to the module that loads them,
# so they have to sit here too. Miss one and the app launches fine and then
# fails at extraction time with a bare ImportError.
for f in s2neighborhood.py s2parser.py s2ngbh.py s2luastate.py s2ltw.py \
         careers.json wants.json; do
    cp "../$f" "$APP/Contents/Resources/$f"
done

cat > "$APP/Contents/Info.plist" <<PLIST
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
    <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>Reads Sims 2 saves read-only.</string>
</dict>
</plist>
PLIST

# Ad-hoc by default, which is enough to run locally. Pass a real identity to get
# something distributable — SIGN_ID="Developer ID Application: …" ./make_app.sh
# Signs last, so the Resources payload is covered by the seal.
SIGN_ID="${SIGN_ID:--}"
if [ "$SIGN_ID" = "-" ]; then
    codesign --force --sign - "$APP" 2>/dev/null || true
else
    codesign --force --deep --timestamp --options runtime \
        --sign "$SIGN_ID" "$APP"
    codesign --verify --strict --verbose=1 "$APP"
fi

echo "Built: $(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"
