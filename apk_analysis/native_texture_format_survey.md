# Native texture format survey (revised from ARM64 disassembly)

## Scope and confidence

This report analyzes the supplied ARM64 engine library `libnba2k20_clean_opt.so` alongside the decompressed object and pixel banks from `GAMEDATA.IFF`, `GOOEYGLOBAL.IFF`, and `GOOEYFRONTEND.IFF`.

The IFF pointer rule and the native per-format byte-size rules are confirmed against the binary. Texture-candidate counts are structural/bounds-filtered candidates, not a claim that every hit is a genuine standalone texture object. For block formats, the native block geometry and byte count are confirmed, but their compressed codec identities are not yet established.

## Confirmed IFF pointer resolution

For a VCTEXTURE descriptor in the decompressed object bank:

- The paired type-2 file entry is selected by the greatest `object_base` not greater than the descriptor offset; the descriptor must remain within that entry's object range.
- The entry's second offset is the pixel-bank base.
- The 32-bit pointer word at descriptor offset `+0xD8` is encoded as relative byte offset plus one; zero is null.
- Therefore `pixel_offset = paired_entry.pixel_base + (pointer_word - 1)`.

This has been validated on three IFFs and by coherent texture recovery. It is the critical correction for the striped exports: reading pixel data relative to the wrong base can return plausible-looking but unrelated bytes.

## Native sizing implementation

Useful dynamic symbols in the ARM64 shared library include:

- `VCTexture_ComputePixelDataSizeAndAlignment` (format overload) at `0x1E6439C`
- `VCTexture_ComputePixelDataSizeAndAlignment` (GPU-vector-format overload) at `0x1E6465C`
- `VCTexture_Init` at `0x1E64728`
- `VCTexture_GetPixelDataSize` at `0x1E644D0`
- `VCTexture_GetPixelDataOffsetForOneMipMapLevel` at `0x1E64C5C`
- `VCTexture_GetPixelDataSizeForOneMipMapLevel` at `0x1E66A38`

The main per-mip size routine is at `0x1E6AE94` (in the disassembly it is adjacent to the preceding exported function). It reads the format ID at `VCTEXTURE+0x10`, packed width/height at `+0xC8`, and depth at `+0xCC`. It looks up a native bits-per-texel table and uses 4x4 block rounding for IDs 15–18.

The confirmed bits-per-texel table values for IDs 1–19 are:

| Format ID | Bits per texel |
|---:|---:|
| 1 | 32 |
| 2 | 16 |
| 3 | 16 |
| 4 | 16 |
| 5 | 8 |
| 6 | 8 |
| 7 | 16 |
| 8 | 32 |
| 9 | 16 |
| 10 | 16 |
| 11 | 16 |
| 12 | 16 |
| 13 | 32 |
| 14 | 24 |
| 15 | 4 |
| 16 | 8 |
| 17 | 16 |
| 18 | 4 |
| 19 | 0 |

For ordinary linear formats, mip payload bytes are calculated from the mip dimensions and bits-per-texel, rounded up to a byte. For IDs 15–18, each dimension is rounded up to complete 4x4 blocks and bytes are calculated from 16 texels per block. Thus the native mip-0 block sizes are:

- ID 15: 8 bytes per 4x4 block
- ID 16: 16 bytes per 4x4 block
- ID 17: 32 bytes per 4x4 block
- ID 18: 8 bytes per 4x4 block

The per-mip offset function at `0x1E64C5C` applies subresource/mip alignment while accumulating preceding levels. A mip-0 export starts at the resolved pixel pointer; this diagnostic resolver exports mip 0 only and does not yet reconstruct complete mip chains, texture arrays, or cube-face ordering.

The separate platform-specific-format helper classifies IDs 15–19 as platform-specific with 16 vectors per block. ID 19 is exceptional: the size table gives it zero bits per texel, while the ordinary size routine's 4x4-block branch covers only IDs 15–18. It must remain unresolved rather than be treated as a normal 8-byte block format.

## Native color conversion evidence

The exported `VCGpuVectorFormat_DecodeColorArrayQuick` function at `0x1F1DB1C` dispatches on format IDs 1–11. Its disassembled cases show:

- ID 1: copies each 32-bit texel unchanged into the native color array.
- ID 2: expands a packed 16-bit RGB color to 32-bit color; the bit extraction matches RGB565.
- ID 3: expands a packed 16-bit 5/5/5/1 color.
- ID 4: expands a packed 16-bit four-nibble color (RGBA4444).
- ID 5: converts one source byte to white RGB with that byte as alpha (A8).
- ID 6: copies one source byte into the low byte of the native color value (single-channel R8-style data).
- ID 7: has a separate 16-bit conversion path, but its exact channel packing is not assigned here.

The 32-bit copy case and the 16-bit/8-bit conversion paths are native behavior; PNG byte-channel ordering is a separate question. The resolver only writes PNG previews where the channel mapping is sufficiently established for practical inspection. Format 7 and block formats are preserved as raw bytes.

## IFF candidate scan

The candidate scan is performed on the decompressed object allocation, at 16-byte aligned offsets. It checks plausible format IDs, dimensions, mip counts, a non-null pointer, the paired-file-entry resolution rule, and the native mip-0 byte range against the pixel allocation. This is a candidate filter, not proof that every match is a real VCTEXTURE object.

Candidate counts and format-ID distributions:

| IFF | Candidates | Format-ID histogram |
|---|---:|---|
| GAMEDATA | 163 | 1:9, 2:8, 4:6, 5:1, 6:9, 7:4, 15:23, 16:103 |
| GOOEYGLOBAL | 35 | 2:3, 4:7, 6:1, 15:4, 16:20 |
| GOOEYFRONTEND | 242 | 2:29, 4:26, 6:5, 7:4, 15:43, 16:135 |

Selected records:

| IFF | Object offset | Hash | Format | Dimensions | Pointer word | Entry |
|---|---:|---|---:|---:|---:|---:|
| GAMEDATA | `0x1E330` | `B3B281A0` | 2 | 1024x256 | `0x1` | 148 |
| GAMEDATA | `0x1E420` | `12E076C2` | 6 | 512x128 | `0xAAAB1` | 148 |
| GAMEDATA | `0x1E510` | `06B6E43E` | 6 | 1024x256 | `0xC0011` | 148 |
| GOOEYGLOBAL | `0x1075F0` | `3750CE31` | 4 | 256x128 | `0x1` | 14 |
| GOOEYGLOBAL | `0x1079B0` | `082B8DFB` | 2 | 128x32 | `0x22801` | 14 |
| GOOEYGLOBAL | `0x108400` | `63D512C1` | 2 | 1024x512 | `0x256801` | 14 |
| GOOEYFRONTEND | `0x7943D0` | `4C7172DB` | 2 | 512x256 | `0x1` | 136 |
| GOOEYFRONTEND | `0x7945B0` | `35ADCA7F` | 2 | 512x256 | `0x1` | 97 |
| GOOEYFRONTEND | `0x794C40` | `465D04D0` | 4 | 1024x512 | `0x1` | 126 |

The histogram above supersedes the older exploratory scan in this file's previous revision. The old scan had missed many ID-16 candidates and must not be used as a count of the current candidate set.

## Extraction validation

The updated diagnostic resolver was run against these records:

- GAMEDATA `0x1E330`, format 2, 1024x256: RGB565 PNG output, 524,288 source bytes, coherent stadium lighting/ceiling imagery.
- GAMEDATA `0x1E420`, format 6, 512x128: 65,536 source bytes, coherent single-channel preview.
- GOOEYFRONTEND `0x794C40`, format 4, 1024x512: RGBA4444 PNG output, 1,048,576 source bytes; agrees with earlier recovered texture.
- Additional format-2 exports from GOOEYGLOBAL and GOOEYFRONTEND also produce coherent images rather than striped pixels.

These tests support both the paired-entry pointer calculation and the RGB565/RGBA4444 extraction paths. The R8 PNG is a diagnostic grayscale visualization; its original one-byte-per-texel source payload is preserved separately.

## Current resolver behavior and remaining work

The updated `iff_texture_resolver.py` supports exact payload sizing for IDs 1–7 and block payload sizing for IDs 15–18. It decodes preview PNGs for IDs 2, 4, 5 and 6, and preserves unsupported channel layouts/block data as raw bytes. It validates mip counts but only exports mip 0.

Remaining work before integrating this into the APK tool:

1. Determine exact channel ordering for ID 1 and exact packed channels for ID 7.
2. Identify and validate the codecs for block formats 15–18; do not infer ETC2 merely from the 8/16-byte block sizes.
3. Reconstruct the full mip/array/cube-face layout including per-subresource alignment.
4. Test the updated resolver against additional IFFs and reference textures, then port the confirmed logic into the application.

The source IFF/APK files are never modified by this diagnostic resolver.
