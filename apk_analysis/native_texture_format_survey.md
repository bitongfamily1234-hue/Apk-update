# Texture format survey (candidate scan; not codec identification)

## Native reference

The original ARM64 engine library `libnba2k20_clean_opt.so` exports:

- `VCTexture_ComputePixelDataSizeAndAlignment(VCTEXTURE_FORMAT, ...)`
- `VCTexture_ComputePixelDataSizeAndAlignment(VCGPUVECTORFORMAT, ...)`
- `VCTexture_ComputePixelDataSizeAndAlignmentAdvanced(...)`
- `VCTexture_ComputePixelDataSizeAndAlignmentForArrayOfTextures(...)`
- `VCTexture_GetPixelDataSize(VCTEXTURE)`
- `VCTexture_GetPixelDataOffsetForOneMipMapLevel(VCTEXTURE, mip, ...)`

The wrapper disassembly at `0x1E6439C` calls native texture initialization, then the per-mip offset routine, and stores the returned data-size/offset values. This is the correct native reference for format-specific size/alignment and mip calculations. The shared library is ARM64; GNU objdump on the current host cannot disassemble it, but LLVM objdump can.

## Heuristic candidate scan

A broad scan at four-byte steps is noisy because the descriptor bank contains non-texture data and zero/false positives. A stricter scan additionally required:
- candidate format integer in 1–15;
- dimensions from 16 through 4096;
- mip count 1–16;
- nonzero encoded pointer;
- resolved offset and estimated mip-0 byte range inside the pixel allocation.

This remains a candidate filter, not definitive proof that each hit is a real texture descriptor. Format meanings and compressed-size estimates have not been confirmed for the non-format-4 values.

| IFF | Candidate records | Candidate format histogram |
|---|---:|---|
| GAMEDATA | 60 | 1:9, 2:8, 4:6, 5:1, 6:9, 7:4, 15:23 |
| GOOEYGLOBAL | 15 | 2:3, 4:7, 6:1, 15:4 |
| GOOEYFRONTEND | 106 | 2:28, 4:26, 6:5, 7:4, 15:43 |

## Sample candidate records (not decoded/verified)

GAMEDATA:
- object `0x1E330`, candidate format 2, 1024x256, encoded pointer `0x1`, paired file entry 148
- object `0x1E420`, candidate format 6, 512x128, encoded pointer `0xAAAB1`, paired entry 148
- object `0x1E510`, candidate format 6, 1024x256, encoded pointer `0xC0011`, paired entry 148

GOOEYGLOBAL:
- object `0x1075F0`, format 4, 256x128, encoded pointer `0x1` (already recovered)
- object `0x1079B0`, candidate format 2, 128x32, encoded pointer `0x22801`
- object `0x108400`, candidate format 2, 1024x512, encoded pointer `0x256801`

GOOEYFRONTEND:
- object `0x7943D0`, candidate format 2, 512x256, encoded pointer `0x1`
- object `0x7945B0`, candidate format 2, 512x256, encoded pointer `0x1`

The format labels above are the raw descriptor values and must not be interpreted as ETC2, RGBA8888, or another codec without evidence.

## Next verification sequence

1. Identify actual format enum values and size/alignment rules from the native function or its callers.
2. Confirm candidate descriptors by their hashes/structure and resource-table relationships rather than dimensions alone.
3. Recover exact mip-0 size, mip chain, array-layer and alignment calculations.
4. Decode a candidate using the verified codec and compare it with the supplied screenshots/reference exports.
5. Only then integrate the general resolver into the application.
