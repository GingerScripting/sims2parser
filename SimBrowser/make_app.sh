#!/bin/bash
# Build "Sim Browser.app" and "Sim Studio.app" from the SwiftPM package.
#
#   ./make_app.sh                 both apps, universal
#   ARCHS=native ./make_app.sh    this machine only — much quicker while iterating
#   APPS=SimStudio ./make_app.sh  just one of them
set -euo pipefail
cd "$(dirname "$0")"

MIN_MACOS=13.0
APPS="${APPS:-SimBrowser SimStudio}"

# Universal by default, so the app runs on Intel as well as Apple silicon.
#
# Built one slice at a time and lipo'd together rather than with `swift build
# --arch arm64 --arch x86_64`, which routes through xcbuild and so needs full
# Xcode. --triple only needs the Command Line Tools, which is all the README
# asks people to install.
if [ "${ARCHS:-universal}" = native ]; then
    swift build -c release
else
    for arch in arm64 x86_64; do
        swift build -c release --triple "$arch-apple-macosx$MIN_MACOS"
    done
fi

# The Python side rides along inside each bundle, so the app does not care
# where the repo was cloned. Each list is the entry script plus its full
# import closure — careers.json and wants.json are read relative to the
# module that loads them, so they have to sit here too. Miss one and the app
# launches fine and then fails at extraction time with a bare ImportError.
BROWSER_FILES="s2neighborhood.py s2parser.py s2ngbh.py s2luastate.py s2ltw.py careers.json wants.json"
STUDIO_FILES="s2studio.py s2package.py s2tools.py s2object.py s2writer.py s2parser.py s2doctor.py \
              s2clone.py s2texture.py s2mesh.py s2ngbh.py \
              s2neighborhood.py s2luastate.py s2ltw.py careers.json wants.json"

build_bundle() {
    local product="$1" name="$2" bundle_id="$3" files="$4" extra_plist="$5"
    local app="../$name.app"
    local bin

    if [ "${ARCHS:-universal}" = native ]; then
        bin=".build/release/$product"
    else
        bin="/tmp/simbrowser-universal-$product.$$"
        lipo -create -output "$bin" \
            ".build/arm64-apple-macosx/release/$product" \
            ".build/x86_64-apple-macosx/release/$product"
    fi

    rm -rf "$app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    cp "$bin" "$app/Contents/MacOS/$product"
    [ "${ARCHS:-universal}" = native ] || rm -f "$bin"

    for f in $files; do
        cp "../$f" "$app/Contents/Resources/$f"
    done

    cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$name</string>
    <key>CFBundleDisplayName</key><string>$name</string>
    <key>CFBundleExecutable</key><string>$product</string>
    <key>CFBundleIdentifier</key><string>$bundle_id</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
    <key>NSHighResolutionCapable</key><true/>
$extra_plist
</dict>
</plist>
PLIST

    # Ad-hoc by default, which is enough to run locally. Pass a real identity
    # to get something distributable — SIGN_ID="Developer ID Application: …"
    # Signs last, so the Resources payload is covered by the seal.
    local sign_id="${SIGN_ID:--}"
    if [ "$sign_id" = "-" ]; then
        codesign --force --sign - "$app" 2>/dev/null || true
    else
        codesign --force --deep --timestamp --options runtime --sign "$sign_id" "$app"
        codesign --verify --strict --verbose=1 "$app"
    fi
    echo "Built: $(cd "$(dirname "$app")" && pwd)/$(basename "$app")"
}

BROWSER_PLIST='    <key>NSHumanReadableCopyright</key><string>Reads Sims 2 saves read-only.</string>'

# Sim Studio registers itself as an editor for .package so Finder can open
# one into it. Neighborhood saves and the game install still open read-only
# inside the app; the daemon refuses to write them.
STUDIO_PLIST='    <key>NSHumanReadableCopyright</key><string>Edits standalone Sims 2 packages. Never writes to a save.</string>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key><string>Sims 2 Package</string>
            <key>CFBundleTypeRole</key><string>Editor</string>
            <key>LSHandlerRank</key><string>Alternate</string>
            <key>LSItemContentTypes</key>
            <array><string>org.macadmins.sims2.package</string></array>
        </dict>
    </array>
    <key>UTExportedTypeDeclarations</key>
    <array>
        <dict>
            <key>UTTypeIdentifier</key><string>org.macadmins.sims2.package</string>
            <key>UTTypeDescription</key><string>Sims 2 Package</string>
            <key>UTTypeConformsTo</key>
            <array><string>public.data</string></array>
            <key>UTTypeTagSpecification</key>
            <dict>
                <key>public.filename-extension</key>
                <array><string>package</string></array>
            </dict>
        </dict>
    </array>'

for app in $APPS; do
    case "$app" in
        SimBrowser) build_bundle SimBrowser "Sim Browser" org.macadmins.rebecca.simbrowser "$BROWSER_FILES" "$BROWSER_PLIST" ;;
        SimStudio)  build_bundle SimStudio  "Sim Studio"  org.macadmins.rebecca.simstudio "$STUDIO_FILES" "$STUDIO_PLIST" ;;
        *) echo "unknown app $app" >&2; exit 2 ;;
    esac
done
