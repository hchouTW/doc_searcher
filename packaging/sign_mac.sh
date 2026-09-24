#!/usr/bin/env bash
# Purpose: Developer ID-sign, notarize, and staple a built DocSearcher.app zip (release CI only).
# What the code does:
#   - Unzips the .app, imports the signing certificate into a throw-away keychain, signs the bundle
#     with the hardened runtime and packaging/entitlements.plist, and verifies the signature.
#   - Submits the app to Apple notarization, fails unless the result is "Accepted", staples the
#     ticket, checks Gatekeeper (spctl), and replaces the input zip with the signed app.
# Usage notes, dependencies, or assumptions:
#   - packaging/sign_mac.sh dist/DocSearcher-macOS-arm64.zip   (macOS runner, Xcode tools)
#   - Environment: MACOS_CERTIFICATE_P12_BASE64, MACOS_CERTIFICATE_PASSWORD,
#     MACOS_SIGNING_IDENTITY ("Developer ID Application: Name (TEAMID)"), APPLE_ID,
#     APPLE_TEAM_ID, APPLE_APP_PASSWORD (app-specific password). Nothing is printed from them.
#   - Any failure exits non-zero, which blocks the release.

set -euo pipefail
cd "$(dirname "$0")/.."

zip_path="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
for name in MACOS_CERTIFICATE_P12_BASE64 MACOS_CERTIFICATE_PASSWORD MACOS_SIGNING_IDENTITY \
            APPLE_ID APPLE_TEAM_ID APPLE_APP_PASSWORD; do
    if [[ -z "${!name:-}" ]]; then
        echo "[sign_mac] missing required secret: $name" >&2
        exit 1
    fi
done

work="$(mktemp -d)"
keychain="$work/signing.keychain-db"
keychain_password="$(uuidgen)"
cleanup() {
    security delete-keychain "$keychain" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

ditto -x -k "$zip_path" "$work/unpacked"
app="$work/unpacked/DocSearcher.app"

security create-keychain -p "$keychain_password" "$keychain"
security set-keychain-settings -lut 3600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
printf '%s' "$MACOS_CERTIFICATE_P12_BASE64" | base64 --decode > "$work/certificate.p12"
security import "$work/certificate.p12" -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" \
    -T /usr/bin/codesign >/dev/null
rm -f "$work/certificate.p12"
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain" >/dev/null
# shellcheck disable=SC2046  # existing keychain list is intentionally word-split
security list-keychains -d user -s "$keychain" $(security list-keychains -d user | tr -d '"')

echo "[sign_mac] signing $(basename "$app")"
codesign --force --deep --timestamp --options runtime \
    --entitlements packaging/entitlements.plist \
    --sign "$MACOS_SIGNING_IDENTITY" "$app"
codesign --verify --deep --strict --verbose=2 "$app"

echo "[sign_mac] notarizing (this can take several minutes)"
ditto -c -k --sequesterRsrc --keepParent "$app" "$work/notarize.zip"
xcrun notarytool submit "$work/notarize.zip" \
    --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" \
    --wait --output-format json > "$work/notary.json"
status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$work/notary.json")"
if [[ "$status" != "Accepted" ]]; then
    echo "[sign_mac] notarization status: ${status:-unknown}" >&2
    cat "$work/notary.json" >&2
    exit 1
fi

xcrun stapler staple "$app"
xcrun stapler validate "$app"
spctl --assess --type execute --verbose "$app"

rm -f "$zip_path"
ditto -c -k --sequesterRsrc --keepParent "$app" "$zip_path"
echo "[sign_mac] signed, notarized, and stapled: $zip_path"
