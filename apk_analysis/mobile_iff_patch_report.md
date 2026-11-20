# NBA 2K20 Mobile IFF texture integration patch

## Result

A mobile-only runtime patch is now built and smoke-tested against the application's real compiled Python 3.11 engine modules from `assets/chaquopy/app.imy`.

The original `iffengine.core` code object is embedded unchanged in a small wrapper. After the original module initializes, the wrapper loads `iffengine.mobile_iff_patch` and installs guarded function wrappers. This avoids replacing the full `core.pyc` with decompiler-generated source.

The GitHub Actions build produced:

- Patched `app.imy`: 417,418 bytes
- Updated `build.json`: app.imy SHA-1 set to `abeb9cbe395b831f748407fc538468a8e71617a6`
- Original archive members: 50
- Patched archive members: 51
- Original `iffengine/core.pyc` wrapped; new `iffengine/mobile_iff_patch.pyc` added
- Every other original archive member was checked byte-for-byte and remained unchanged

## Confirmed corrections

### 1. Paired file-entry pixel base

The engine's original mobile IFF path decompresses the object and pixel allocations but does not apply the per-resource type-2 entry's pixel base to the VCTEXTURE-relative pointer. The patch parses the allocation and file-entry tables and remaps standard 2K20 VCTEXTURE payload offsets as:

```text
payload_global =
    pixel_allocation_global_base
  + paired_type2_entry.pixel_base
  + (VCTEXTURE_pointer_word - 1)
```

It validates the descriptor against the owning object range and the complete mip-0 payload against the pixel allocation. If resolution fails or overlaps another texture payload, that texture is marked unresolved rather than exported from the old guessed offset.

### 2. Native format 6 is R8, not ETC1

The app's original `FORMAT_ETC1_RGB = 6` conflicts with the native NBA 2K20 Mobile format table. Native format 6 is one byte per texel. The original app calculates an 8-byte-per-4x4-block size for it, which is four times too small for the observed 512×128 record:

- Native size: 512 × 128 = 65,536 bytes
- Old ETC1 calculation: 128 × 32 × 8 = 32,768 bytes

The patch uses the native size table for 2K20 mobile only and gives R8 a grayscale preview and single-channel PNG replacement path. The old legacy format path is not changed for files that do not match the mobile VCTEXTURE layout.

Native format 5 (A8) is also admitted to the mobile descriptor scan and preview/replacement code. The current known IFF samples contain one such exact descriptor in GAMEDATA.

### 3. Preservation and legacy behavior

- Existing decompression, texture-list construction, PNG encoding/decoding for other formats, IFF save/rebuild, OBB, and session code remain in the original engine.
- The wrapper pre-decompresses once to identify the allocation layout, then passes the already-decoded blocks to the original open routine to avoid a second full inflate.
- The wrapper only installs the native layout corrections when the raw magic, allocation IDs, block positions/sizes, and standard VCTEXTURE signatures all validate.
- Other wrapper formats and compact 2K16 headers retain the original app path.
- The patch does not modify `.py.enc` assets, the Kotlin DEX files, or the APK's signing/integrity checks.

## Validation

### Source regression suite

The repository's texture and patch unit tests passed in GitHub Actions.

### Actual compiled-engine smoke test

The patch builder repackaged the existing app.imy and the smoke test imported the resulting bytecode using Python 3.11. It then constructed a small but structurally valid compressed mobile IFF and used the app's actual engine functions to:

1. Open the mobile IFF.
2. Confirm the corrected format-6 payload points to global offset `0x360`, which includes the type-2 entry's `0x40` pixel base.
3. Confirm the exact 32-byte R8 payload size for an 8×4 record.
4. Decode the R8 pixels into a PNG preview.
5. Replace the texture from PNG and verify the single-channel bytes written to the pixel allocation.
6. Save the compressed IFF and reopen it, confirming the decompressed allocation bytes match the edited in-memory data.
7. Decode format 5 as white/alpha A8.

All seven checks passed.

### Supplied IFF allocation audit

The paired-entry offset algorithm was independently tested on the three supplied files:

| IFF | Standard VCTEXTURE descriptors audited | Successfully resolved | Failed |
|---|---:|---:|---:|
| GAMEDATA.IFF | 160 | 160 | 0 |
| GOOEYGLOBAL(1).IFF | 36 | 36 | 0 |
| GOOEYFRONTEND(1).IFF | 240 | 240 | 0 |
| **Total** | **436** | **436** | **0** |

These are records accepted by the mirrored VCTEXTURE signature/format/dimension/pointer scan, including format 5. This is distinct from the broader 440-record 16-byte candidate inventory, which intentionally has looser structural criteria.

The already-recovered reference textures remain consistent with the same pointer calculation:

| IFF | Hash | Format | Dimensions | Payload bytes | Paired entry |
|---|---|---:|---:|---:|---:|
| GAMEDATA | A0C0C743 | 4 | 128×32 | 8,192 | 158 |
| GOOEYGLOBAL | 3750CE31 | 4 | 256×128 | 65,536 | 14 |
| GOOEYFRONTEND | 465D04D0 | 4 | 1024×512 | 1,048,576 | 126 |

These are real-file allocation/payload resolution audits; the full runtime smoke test above uses the actual app bytecode with a synthetic mobile IFF fixture because the original three IFF binaries are not stored in the public repository.

## Important: APK rebuild/signing is still blocked

This repository is an extracted APK resource/Dex collection, not the Android Studio/Gradle project. It contains an `AndroidManifest.xml`, DEX files, libraries and assets, but no `gradlew`, `build.gradle`, `settings.gradle`, Gradle signing configuration, or signing keystore.

The app also has two signing-certificate integrity checks:
- Kotlin/Dex-side `TamperGuard`
- Python-side `iffengine.integrity`, which checks the installed APK certificate against hashes in `iffengine/integrity_constants.pyc`

The build artifact deliberately leaves these checks unchanged. Updating `build.json` only refreshes the Chaquopy `app.imy` content hash; it does not re-sign the APK or regenerate the certificate allow-list. Therefore **the patched app.imy is a tested integration artifact, not yet an installable APK**. Rebuilding an APK requires the Android build project and an authorized signing configuration which regenerates both integrity fingerprints as part of a normal build. No integrity gate was bypassed.

## Side lead: other IFF resources

The inventory also documents the 129 type-1 file entries across the three IFFs. Some have opaque headers and embedded resource names, but there is not yet a verified scene/model/OBJ serialization signature. That investigation remains parked as requested; no unverified scene exporter is being represented as functional.

## Files and CI

- `apk_analysis/mobile_iff_patch.py`: runtime patch
- `apk_analysis/patch_app_imy.py`: app.imy wrapper builder and manifest hash update
- `apk_analysis/smoke_test_patched_app.py`: actual compiled-engine synthetic IFF smoke test
- `tests/test_mobile_iff_patch.py`: patcher unit tests
- `.github/workflows/mobile-iff-patch.yml`: build and smoke-test workflow

Successful workflow: https://github.com/bitongfamily1234-hue/Apk-update/actions/runs/44788534086

The workflow artifact is named `nba2k20-mobile-iff-patch` and contains the patched `app.imy`, matching `build.json`, and build report.
