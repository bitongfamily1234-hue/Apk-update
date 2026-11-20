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

## Recovered samples

| IFF | Descriptor | Texture hash | Paired entry | Pixel base | D8 word | Resolved pixel offset | Result |
|---|---:|---|---:|---:|---:|---:|---|
| GAMEDATA.IFF | `0x89790` | `A0C0C743` | 158 | `0x68CF40` | `0xAAC1` | `0x697A00` | recognizable 128x32 NBA 2K logo |
| GOOEYGLOBAL(1).IFF | `0x1075F0` | `3750CE31` | 14 | `0x10FD00` | `0x1` | `0x10FD00` | clean 256x128 “2K NAV” graphic |
| GOOEYFRONTEND(1).IFF | `0x794C40` | `465D04D0` | 126 | `0x9932C0` | `0x1` | `0x9932C0` | coherent 1024x512 texture instead of stripes |

## Independent validation

The resolver was checked against the clean RGBA4444 reference images in the uploaded `decoded_textures_16.zip`. Five record/reference pairs matched the original RGBA4444 pixel bytes exactly:

- `texture_06_4444_256x128.png` → GOOEYFRONTEND record `ED031014`
- `texture_12_4444_512x128.png` → GOOEYFRONT record `008716C0`
- `texture_13_4444_256x128.png` → GOOEYGLOBAL `E4E0E0AC` and GOOEYFRONTEND `E4E0E0AC`
- `texture_15_4444_256x128.png` → GOOEYGLOBAL record `898089E5`

All four reference PNGs are byte-identical after converting them back to RGBA4444; the `texture_13` reference has a duplicate matching record in the other IFF.

Across the three uploaded IFF samples, 42 format-4 records were mapped to in-bounds pixel ranges with this method. The read-only diagnostic script is `iff_texture_resolver.py`.

## Scope and remaining work

This is a validated diagnostic resolver, not yet an APK patch. It currently exports mip 0 of format 4 (RGBA4444), and does not yet iterate array layers or mip chains.

Next:
1. apply the same per-file base resolution to ETC2 RGB, ETC2 RGBA8/EAC, RGB565, and raw formats;
2. verify mip/array offset calculations against the native texture routines;
3. port the resolver into the actual TKM/Chaquopy runtime code path;
4. replace the ARM64 placeholder copies in the other ABI folders with genuine architecture-specific binaries.

The original IFF samples and APK assets were left unchanged.
