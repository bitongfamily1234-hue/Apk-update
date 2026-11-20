#!/usr/bin/env python3
"""Resolve NBA 2K20 Mobile VCTEXTURE pointers through paired IFF file entries.

Supported base-level payload formats:
  4  - RGBA4444 (decoded to PNG)
  15 - ETC2 RGB8 (raw 4x4-block payload exported)
  16 - ETC2 EAC RGBA8 (raw 4x4-block payload exported)

This is a diagnostic extractor. It never modifies the source IFF or APK.
"""
from __future__ import annotations

import argparse
import bisect
import json
import struct
import zlib
from pathlib import Path
from typing import Any

from PIL import Image

IFF_MAGIC = bytes.fromhex("94ef3bff")
OBJECT_BANK_ID = 0xBB05A9C1
PIXEL_BANK_ID = 0x411536D5
VCTEXTURE_MIN_SIZE = 0xDC

# Block sizes are bytes per 4x4 texel block.
FORMAT_INFO = {
    4: {"name": "RGBA4444", "kind": "linear", "bytes_per_pixel": 2},
    15: {"name": "ETC2_RGB8", "kind": "etc2", "bytes_per_block": 8},
    16: {"name": "ETC2_EAC_RGBA8", "kind": "etc2", "bytes_per_block": 16},
}


def _read_iff(path: str | Path) -> tuple[bytes, bytes, list[dict[str, int]]]:
    raw = Path(path).read_bytes()
    if raw[:4] != IFF_MAGIC:
        raise ValueError(f"{path}: unsupported IFF magic {raw[:4].hex()}")

    sub_count = struct.unpack_from("<Q", raw, 0x10)[0]
    x38_seed = struct.unpack_from("<Q", raw, 0x18)[0]
    file_count = struct.unpack_from("<Q", raw, 0x20)[0]
    first_subhead = struct.unpack_from("<Q", raw, 0x28)[0]
    subheader_table = x38_seed + 23
    file_table = 40 + first_subhead - 1

    banks: dict[int, bytes] = {}
    for i in range(sub_count):
        aid, _id1, _kind, size, comp_size, start, span, _reserved = struct.unpack_from(
            "<IIQQQQQQ", raw, subheader_table + i * 56
        )
        header = raw[start : start + 16]
        if header[:4] != b"ZLIB":
            raise ValueError(f"allocation {i}: expected ZLIB header, got {header[:4]!r}")
        uncompressed_size, header_span, header_comp_size = struct.unpack_from(">III", header, 4)
        if header_span != span or header_comp_size != comp_size:
            raise ValueError(f"allocation {i}: subheader/ZLIB size mismatch")
        block = zlib.decompress(raw[start + 16 : start + span])
        if len(block) != size or len(block) != uncompressed_size:
            raise ValueError(f"allocation {i}: decompressed length mismatch")
        banks[aid] = block

    if OBJECT_BANK_ID not in banks or PIXEL_BANK_ID not in banks:
        raise ValueError("IFF is missing the expected VCTEXTURE object or pixel allocation")

    relative_entries = struct.unpack_from(f"<{file_count}Q", raw, file_table)
    paired_entries: list[dict[str, int]] = []
    for index, relative in enumerate(relative_entries):
        entry_at = file_table + relative - 1 + index * 8
        id0, id1, entry_type = struct.unpack_from("<IIQ", raw, entry_at)
        if entry_type != 2:
            continue
        object_base, pixel_base = struct.unpack_from("<QQ", raw, entry_at + 16)
        paired_entries.append(
            {
                "object_base": object_base,
                "pixel_base": pixel_base,
                "index": index,
                "id0": id0,
                "id1": id1,
            }
        )

    if not paired_entries:
        raise ValueError("IFF contains no paired type-2 file entries")
    paired_entries.sort(key=lambda item: (item["object_base"], item["index"]))
    return banks[OBJECT_BANK_ID], banks[PIXEL_BANK_ID], paired_entries


def _entry_for_descriptor(
    descriptor_offset: int, object_data: bytes, entries: list[dict[str, int]]
) -> dict[str, int]:
    starts = [entry["object_base"] for entry in entries]
    index = bisect.bisect_right(starts, descriptor_offset) - 1
    if index < 0:
        raise ValueError("no paired file-entry base at or before this VCTEXTURE descriptor")

    entry = entries[index]
    next_base = None
    for candidate in entries[index + 1 :]:
        if candidate["object_base"] > entry["object_base"]:
            next_base = candidate["object_base"]
            break
    descriptor_end = descriptor_offset + VCTEXTURE_MIN_SIZE
    if descriptor_end > len(object_data):
        raise ValueError("VCTEXTURE descriptor extends beyond object allocation")
    if next_base is not None and descriptor_end > next_base:
        raise ValueError("VCTEXTURE descriptor crosses into the next file-entry range")
    return entry


def _payload_size(format_id: int, width: int, height: int) -> int:
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid texture dimensions {width}x{height}")
    info = FORMAT_INFO.get(format_id)
    if info is None:
        raise ValueError(f"unsupported VCTEXTURE format ID {format_id}")
    if info["kind"] == "linear":
        return width * height * info["bytes_per_pixel"]

    # ETC2 stores complete 4x4 blocks, including partially covered edge blocks.
    block_columns = (width + 3) // 4
    block_rows = (height + 3) // 4
    return block_columns * block_rows * info["bytes_per_block"]


def resolve_texture_payload(
    iff_path: str | Path, descriptor_offset: int
) -> tuple[dict[str, Any], bytes]:
    object_data, pixel_data, entries = _read_iff(iff_path)
    if descriptor_offset < 0 or descriptor_offset + VCTEXTURE_MIN_SIZE > len(object_data):
        raise ValueError("descriptor offset outside object allocation")

    texture_hash = struct.unpack_from("<I", object_data, descriptor_offset)[0]
    format_id = struct.unpack_from("<I", object_data, descriptor_offset + 0x10)[0]
    width, height = struct.unpack_from("<HH", object_data, descriptor_offset + 0xC8)
    mip_count = object_data[descriptor_offset + 0xCE]
    packed_flags = struct.unpack_from("<I", object_data, descriptor_offset + 0xC4)[0]
    pointer_word = struct.unpack_from("<I", object_data, descriptor_offset + 0xD8)[0]

    if mip_count != 1:
        raise ValueError(
            f"descriptor {descriptor_offset:#x} has {mip_count} mip levels; this helper currently exports mip 0 only"
        )
    if pointer_word == 0:
        raise ValueError("VCTEXTURE pixel pointer is null")

    info = FORMAT_INFO.get(format_id)
    if info is None:
        raise ValueError(f"unsupported VCTEXTURE format ID {format_id}")
    size = _payload_size(format_id, width, height)
    entry = _entry_for_descriptor(descriptor_offset, object_data, entries)

    # VCTexture_MakeRelative stores a valid pointer as (relative offset + 1).
    relative_offset = pointer_word - 1
    pixel_offset = entry["pixel_base"] + relative_offset
    pixel_end = pixel_offset + size
    if pixel_offset < 0 or pixel_end > len(pixel_data):
        raise ValueError(
            f"resolved pixel range [{pixel_offset:#x}, {pixel_end:#x}) exceeds pixel allocation size {len(pixel_data):#x}"
        )

    payload = pixel_data[pixel_offset:pixel_end]
    metadata: dict[str, Any] = {
        "iff": Path(iff_path).name,
        "descriptor_offset": hex(descriptor_offset),
        "texture_hash": f"{texture_hash:08X}",
        "format_id": format_id,
        "format_name": info["name"],
        "dimensions": {"width": width, "height": height},
        "mip_count": mip_count,
        "packed_flags": hex(packed_flags),
        "file_entry_index": entry["index"],
        "file_entry_id0": f"{entry['id0']:08X}",
        "file_entry_id1": f"{entry['id1']:08X}",
        "object_base": hex(entry["object_base"]),
        "pixel_base": hex(entry["pixel_base"]),
        "pointer_word": hex(pointer_word),
        "relative_pixel_offset": hex(relative_offset),
        "resolved_pixel_offset": hex(pixel_offset),
        "payload_bytes": size,
    }
    return metadata, payload


def decode_rgba4444(payload: bytes, width: int, height: int) -> Image.Image:
    if len(payload) != width * height * 2:
        raise ValueError("RGBA4444 payload length does not match dimensions")
    rgba = bytearray(width * height * 4)
    for pixel in range(width * height):
        word = payload[pixel * 2] | (payload[pixel * 2 + 1] << 8)
        out = pixel * 4
        rgba[out : out + 4] = bytes(
            (
                ((word >> 12) & 0xF) * 17,
                ((word >> 8) & 0xF) * 17,
                ((word >> 4) & 0xF) * 17,
                (word & 0xF) * 17,
            )
        )
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def export_texture(iff_path: str | Path, descriptor_offset: int, output_path: str | Path) -> dict[str, Any]:
    metadata, payload = resolve_texture_payload(iff_path, descriptor_offset)
    output = Path(output_path)
    fmt = metadata["format_id"]
    width = metadata["dimensions"]["width"]
    height = metadata["dimensions"]["height"]

    if fmt == 4:
        decode_rgba4444(payload, width, height).save(output)
        metadata["output_kind"] = "PNG"
    else:
        # Preserve compressed bytes for the app's ETC2 decoder or an independent validator.
        output.write_bytes(payload)
        metadata["output_kind"] = "raw compressed ETC2 blocks"

    metadata["output"] = str(output)
    sidecar = output.with_suffix(output.suffix + ".json")
    sidecar.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata["sidecar"] = str(sidecar)
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iff", help="source IFF file")
    parser.add_argument("--record-offset", required=True, help="VCTEXTURE descriptor offset, e.g. 0x8A000")
    parser.add_argument("--output", required=True, help="PNG for format 4, raw ETC2 payload for formats 15/16")
    args = parser.parse_args()
    print(json.dumps(export_texture(args.iff, int(args.record_offset, 0), args.output), indent=2))
