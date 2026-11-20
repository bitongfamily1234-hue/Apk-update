# Native VCTEXTURE mip and array-layout notes

## Source inspected

The uploaded ARM64 Android native library `libnba2k20_clean_opt.so` is an AArch64 ELF shared object (Android NDK r16b, Android 27). Relevant dynamic symbols remain available in its symbol table despite the library being stripped.

The following symbol bodies were disassembled from the uploaded library:

| Function | Address | Size |
|---|---:|---:|
| `VCTexture_GetPixelDataForOneMipMapLevel` | `0x1E64C20` | 60 bytes |
| `VCTexture_GetPixelDataOffsetForOneMipMapLevel` | `0x1E64C5C` | 304 bytes |
| `VCTexture_GetPixelDataSizeForOneMipMapLevel` | `0x1E66A38` | 148 bytes |
| `VCTexture_ComputePixelDataSizeAndAlignment` (format overload) | `0x1E6439C` | 224 bytes |
| `VCTexture_ComputePixelDataSizeAndAlignmentForArrayOfTextures` (format overload) | `0x1E6725C` | 224 bytes |

## Confirmed field reads

Both native mip offset/size functions read:
- packed field at VCTEXTURE offset `+0xC4`;
- byte field at VCTEXTURE offset `+0xCE`.

The native code extracts bits 22–25 of the packed `+0xC4` word. It uses that 4-bit value when calculating subresource offsets. This is a layer/array-like layout dimension candidate, but its exact serialized meaning and count bias have not yet been established. Do not treat the nibble as a plain count without further tests.

The byte at `+0xCE` is used by native code as a mip-level bound. The native size-for-one-mip routine derives its result from calls to the native offset routine rather than from a standalone bytes-per-pixel multiplication.

## Alignment evidence

The native array-of-textures size routine ends with this calculation:

`total_size = align_up(per_texture_size, 16) * (texture_count - 1) + per_texture_size`

It also reports alignment `16`. In the disassembly this is the sequence:
- add `0xF` to the per-texture size;
- mask with `0xFFFFFFF0` to round down after adding, equivalent to 16-byte alignment;
- multiply by the number of preceding textures;
- add the unrounded size of the final texture.

This confirms that adjacent array texture allocations may include 16-byte padding between items. Treating an array payload as one tightly packed concatenation can therefore misplace later layers.

The native mip offset function also contains alignment branches using 1-byte and 16-byte alignment values. The exact branch conditions are tied to the packed flags and subresource indices; the helper does not currently emulate them.

## Practical consequence for the resolver

The current diagnostic resolver deliberately rejects records whose mip-count field is not 1. For single-mip textures, the per-file resource base plus pointer-bias correction has been validated against RGBA4444 and ETC2 RGB reference images.

Do not remove that guard or calculate mip offsets by repeatedly halving width/height until the native offset function has been reconstructed. The correct implementation must account for:
1. native mip-index handling;
2. the 4-bit packed subresource dimension at `+0xC4`;
3. block-compressed versus linear format sizes;
4. per-subresource 1/16-byte alignment;
5. the 16-byte inter-texture alignment seen in array allocation.

## Remaining reverse-engineering tasks

- Recover the exact argument semantics of `VCTexture_GetPixelDataOffsetForOneMipMapLevel(texture, arg1, arg2)`.
- Determine whether the packed 4-bit value at `+0xC4` is array count, depth, or an encoded last-index value for each texture type.
- Compare native offset calculations against actual multi-layer/multi-mip IFF payloads.
- Only then extend the Python helper to export complete mip chains and arrays.

Until those checks are done, the helper's one-mip-only restriction is intentional.
