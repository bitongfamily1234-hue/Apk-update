#!/usr/bin/env python3
"""Read-only RGBA4444 texture extractor using per-file IFF allocation offsets.

Tested with the uploaded NBA 2K20 Mobile GAMEDATA / GOOEYGLOBAL /
GOOEYFRONTEND samples. This diagnostic helper extracts mip 0 of format 4.
It does not patch the IFF or the APK.
"""
import argparse
import bisect
import struct
import zlib
from pathlib import Path
from PIL import Image

MAGIC = bytes.fromhex("94ef3bff")


def read_iff(path):
    raw = Path(path).read_bytes()
    if raw[:4] != MAGIC:
        raise ValueError("not a supported IFF container")
    sub_count = struct.unpack_from("<Q", raw, 0x10)[0]
    x38_seed = struct.unpack_from("<Q", raw, 0x18)[0]
    file_count = struct.unpack_from("<Q", raw, 0x20)[0]
    first_subhead = struct.unpack_from("<Q", raw, 0x28)[0]
    x38 = x38_seed + 32 - 8 - 1
    table = 40 + first_subhead - 1

    banks = []
    for i in range(sub_count):
        aid, _id1, kind, size, comp, start, span, _reserved = struct.unpack_from(
            "<IIQQQQQQ", raw, x38 + i * 56
        )
        head = raw[start:start + 16]
        if head[:4] != b"ZLIB":
            raise ValueError(f"allocation {i}: unsupported compression header")
        usize, hspan, hcomp = struct.unpack_from(">III", head, 4)
        if hspan != span or hcomp != comp:
            raise ValueError(f"allocation {i}: header mismatch")
        data = zlib.decompress(raw[start + 16:start + span])
        if len(data) != size or len(data) != usize:
            raise ValueError(f"allocation {i}: decompressed-size mismatch")
        banks.append((aid, data))

    offsets = struct.unpack_from(f"<{file_count}Q", raw, table)
    paired = []
    for i, rel in enumerate(offsets):
        p = table + rel - 1 + i * 8
        id0, id1, typ = struct.unpack_from("<IIQ", raw, p)
        if typ == 2:
            a, b = struct.unpack_from("<QQ", raw, p + 16)
            paired.append((a, b, i, id0, id1))
    paired.sort(key=lambda x: x[0])

    by_id = {aid: data for aid, data in banks}
    object_bank = by_id.get(0xBB05A9C1, banks[0][1])
    pixel_bank = by_id.get(0x411536D5, banks[1][1])
    if not paired:
        raise ValueError("no paired type-2 file entries")
    return object_bank, pixel_bank, paired


def extract_rgba4444(iff_path, descriptor_offset, output_path):
    objects, pixels, entries = read_iff(iff_path)
    if descriptor_offset < 0 or descriptor_offset + 0xDC > len(objects):
        raise ValueError("descriptor offset outside object allocation")

    fmt = struct.unpack_from("<I", objects, descriptor_offset + 0x10)[0]
    if fmt != 4:
        raise ValueError(f"format {fmt} is not RGBA4444 (format 4)")
    packed = struct.unpack_from("<I", objects, descriptor_offset + 0xC4)[0]
    width, height = struct.unpack_from("<HH", objects, descriptor_offset + 0xC8)
    mip_count = struct.unpack_from("<H", objects, descriptor_offset + 0xCE)[0]
    pointer = struct.unpack_from("<I", objects, descriptor_offset + 0xD8)[0]
    if not width or not height or not pointer:
        raise ValueError("invalid dimensions or null pixel pointer")
    if mip_count != 1:
        raise ValueError("diagnostic extractor currently supports mip_count=1 only")

    starts = [e[0] for e in entries]
    ix = bisect.bisect_right(starts, descriptor_offset) - 1
    if ix < 0:
        raise ValueError("no paired file-entry base before descriptor")
    obj_base, pixel_base, entry_index, id0, id1 = entries[ix]
    end = starts[ix + 1] if ix + 1 < len(starts) else len(objects)
    if not obj_base <= descriptor_offset < end:
        raise ValueError("descriptor is outside the selected file-entry range")

    # The native VCTEXTURE pointer is encoded as (relative_offset + 1).
    relative_offset = pointer - 1
    pixel_offset = pixel_base + relative_offset
    byte_count = width * height * 2
    if pixel_offset + byte_count > len(pixels):
        raise ValueError("resolved pixel range exceeds pixel allocation")

    src = pixels[pixel_offset:pixel_offset + byte_count]
    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        word = src[2 * i] | (src[2 * i + 1] << 8)
        j = 4 * i
        rgba[j:j + 4] = bytes(
            ((word >> 12 & 15) * 17, (word >> 8 & 15) * 17,
             (word >> 4 & 15) * 17, (word & 15) * 17)
        )

    Image.frombytes("RGBA", (width, height), bytes(rgba)).save(output_path)
    return {
        "descriptor_offset": hex(descriptor_offset),
        "dimensions": f"{width}x{height}",
        "format": fmt,
        "mip_count": mip_count,
        "array_count_field": (packed >> 22) & 15,
        "file_entry_index": entry_index,
        "file_entry_id0": f"{id0:08X}",
        "file_entry_id1": f"{id1:08X}",
        "object_base": hex(obj_base),
        "pixel_base": hex(pixel_base),
        "pointer_word": hex(pointer),
        "relative_pixel_offset": hex(relative_offset),
        "resolved_pixel_offset": hex(pixel_offset),
        "pixel_bytes": byte_count,
        "output": str(output_path),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("iff")
    ap.add_argument("--record-offset", required=True, help="VCTEXTURE descriptor offset, e.g. 0x89790")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    print(extract_rgba4444(args.iff, int(args.record_offset, 0), args.output))
