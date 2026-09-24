# Code signing and notarization (Task 6.2)

Releases are **unsigned until you opt in**. Unsigned builds keep working exactly as before
(branch builds are always unsigned). Once enabled, a release is published only if every
artifact is signed, notarized (macOS), and passes its packaged self-check again after signing.

## What runs

`.github/workflows/release.yml` → job `sign` (after `build`, before `publish`):

| Platform | Script | Checks that must pass |
| --- | --- | --- |
| macOS arm64 | `packaging/sign_mac.sh` | `codesign --verify --deep --strict`, notarization status `Accepted`, `stapler validate`, `spctl --assess`, `scripts/verify_build.py` |
| Windows x64 | `packaging/sign_win.ps1` | `Get-AuthenticodeSignature` = `Valid`, `scripts/verify_build.py` |

macOS uses the hardened runtime with `packaging/entitlements.plist`
(`allow-unsigned-executable-memory`, `disable-library-validation`, which the bundled Python
interpreter and third-party dylibs need).

## One-time setup

1. **Environment:** Settings → Environments → New `release-signing`. Under *Deployment branches
   and tags* choose *Selected* and allow only tags `v*`; optionally add required reviewers.
   Secrets in an environment are never exposed to pull requests (including forks), and this job
   only runs on tag pushes or manual dispatch.
2. **Environment secrets** (in `release-signing`, not repository secrets):

   | Secret | Value |
   | --- | --- |
   | `MACOS_CERTIFICATE_P12_BASE64` | `base64 -i DeveloperID.p12` of the *Developer ID Application* certificate + private key |
   | `MACOS_CERTIFICATE_PASSWORD` | password of that `.p12` |
   | `MACOS_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID1234)` |
   | `APPLE_ID` | Apple ID used for notarization |
   | `APPLE_TEAM_ID` | 10-character team ID |
   | `APPLE_APP_PASSWORD` | app-specific password from appleid.apple.com |
   | `WINDOWS_CERTIFICATE_PFX_BASE64` | base64 of the Authenticode `.pfx` (OV/EV code-signing) |
   | `WINDOWS_CERTIFICATE_PASSWORD` | password of that `.pfx` |

   Optional variable `WINDOWS_TIMESTAMP_URL` (default `http://timestamp.digicert.com`).
   Hardware-token EV certificates cannot be exported to a `.pfx`; they need a cloud signing
   service (e.g. Azure Trusted Signing), which would replace `sign_win.ps1`.
3. **Turn it on:** Settings → Variables → Repository variable `SIGNING_ENABLED` = `true`.
4. **Dry run first:** Actions → *Release* → *Run workflow* with an existing tag and `publish`
   unticked. The signed artifacts and checksums are uploaded as `release-dry-run`.

## Verify a published release

```bash
ditto -x -k DocSearcher-macOS-arm64.zip . && \
codesign --verify --deep --strict --verbose=2 DocSearcher.app && \
spctl --assess --type execute --verbose DocSearcher.app
shasum -a 256 -c SHA256SUMS.txt
```

```powershell
Get-AuthenticodeSignature .\DocSearcher-Windows-x64.exe
```

## Status

Implemented and linted (actionlint) but **not executed**: no signing credentials were
available while writing it. The first dry run with real credentials is the acceptance test.
