# IFF per-resource texture pointer recovery

## Finding

For the tested NBA 2K20 Mobile IFF files, a VCTEXTURE pixel pointer is not sufficient on its own. The pointer is relative to the pixel-data base of the file/resource containing that descriptor.

The file-entry pointer table uses the adjustment:

`entry_address = file_table_offset + relative_entry_offset - 1 + (8 * entry_index)`

A type-2 file entry contains paired offsets:
- `values[0]`: corresponding start in the object/descriptor allocation;
- `values[1]`: corresponding start in the pixel-data allocation.

For a VCTEXTURE descriptor, select the paired type-2 entry whose object start is the greatest value not exceeding the descriptor address, and confirm the descriptor lies before the next paired object start.

The native VCTEXTURE pixel pointer is stored as `relative_offset + 1`. Therefore:

`resolved_pixel_offset = paired_entry.values[1] + (VCTEXTURE_D8 - 1)`

Using only the global pixel-allocation start is wrong for these records and produces striped or unrelated exports.

## Recovered RGBA4444 samples

| IFF | Descriptor | Texture hash | Paired entry | Pixel base | D8 word | Resolved pixel offset | Result |
|---|---:|---|---:|---:|---:|---:|---|
| GAMEDATA.IFF | `0x89790` | `A0C0C743` | 158 | `0x68CF40` | `0xAAC1` | `0x697A00` | recognizable 128x32 NBA 2K logo |
| GOOEYGLOBAL(1).IFF | `0x1075F0` | `3750CE31` | 14 | `0x10FD00` | `0x1` | `0x10FD00` | clean 256x128 “2K NAV” graphic |
| GOOEYFRONTEND(1).IFF | `0x794C40` | `465D04D0` | 126 | `0x9932C0` | `0x1` | `0x9932C0` | coherent 1024x512 texture instead of stripes |

## Reference-image validation

The resolver was checked against the clean RGBA4444 reference images in the uploaded `decoded_textures_16.zip`. Five record/reference pairs matched the original RGBA4444 pixel bytes exactly:

- `texture_06_4444_256x128.png` → GOOEYFRONTEND record `ED031014`
- `texture_12_4444_512x128.png` → GOOEYFRONT record `008716C0`
- `texture_13_4444_256x128.png` → GOOEYGLOBAL `E4E0E0AC` and GOOEYFRONTEND `E4E0E0AC`
- `texture_15_4444_256x128.png` → GOOEYGLOBAL record `898089E5`

Across the three uploaded IFF samples, 42 format-4 records were mapped to in-bounds pixel ranges.

### ETC2 RGB8 (format ID 15)

Two ETC2 RGB references were decoded independently using Mesa OpenGL's ETC2 decoder and matched pixel-for-pixel (RGBA):

| Reference | IFF | Descriptor | Hash | File entry | Pixel base | Pointer word | Resolved pixel offset | Exact pixels |
|---|---|---:|---|---:|---:|---:|---:|---:|
| `texture_08_etc2_64x64.png` | GAMEDATA.IFF | `0x8A000` | `7F5BBCA8` | 158 | `0x68CF40` | `0x18EB31` | `0x81BA70` | 4096 / 4096 |
| `texture_14_etc2_64x64.png` | GAMEDATA.IFF | `0x63630` | `FDFE5138` | 5 | `0x48CB60` | `0x115591` | `0x5A20F0` | 4096 / 4096 |

For these tests, format ID 15 uses 8 bytes per 4x4 block. The block payload size is `ceil(width/4) * ceil(height/4) * 8`.

### ETC2 EAC RGBA8 (format ID 16)

Format ID 16 records use 16 bytes per 4x4 block and are now resolved/exported as raw compressed payloads. One leading candidate, GAMEDATA record `12A83209` at `0x8A0F0`, resolves to pixel offset `0x81C530` and has 4096 payload bytes for a 64x64 image.

That candidate is visually related to the corresponding `texture_04_etc2a_64x64.png` reference, but it is **not an exact match**: RGB mean absolute error is about 14.03, alpha mean absolute error about 8.49, and only 1754 of 4096 pixels match exactly. This is recorded as a lead, not as a confirmed recovery. Other ETC2A reference candidates in this sample set also remain non-exact or have no dimension-matched candidate.

For format ID 16, the block payload size is `ceil(width/4) * ceil(height/4) * 16`. More work is needed to establish whether the remaining discrepancy is due to a different source IFF, block ordering/variant, or texture metadata interpretation.

## Diagnostic resolver

`apk_analysis/iff_texture_resolver.py` now supports:
- format 4: RGBA4444 → PNG;
- format 15: ETC2 RGB8 → raw compressed blocks;
- format 16: ETC2 EAC RGBA8 → raw compressed blocks.

ETC2 is exported in its original compressed form rather than decoded by the helper, so the bytes can be passed to TKM's decoder or to an independent ETC2 validator. The resolver currently handles one mip level only; it rejects records whose mip-count field is not 1.

The block-size calculation uses complete 4x4 blocks, including edge blocks for dimensions not divisible by four.

## Scope and remaining work

This is a validated diagnostic resolver, not yet an APK patch. Remaining work:
1. match ETC2 EAC RGBA8 references exactly and check for alternate block ordering or variant handling;
2. support and validate mip chains and array layers using the native texture routines;
3. investigate RGB565 and other raw formats;
4. port the per-resource base calculation into the actual TKM/Chaquopy runtime code path;
5. replace the ARM64 placeholder copies in the other ABI folders with genuine architecture-specific binaries.

The original IFF samples and APK assets were left unchanged.
