#!/usr/bin/env python3
"""Resolve NBA 2K20 Mobile VCTEXTURE pointers through paired IFF file entries.

Diagnostic only: source IFF/APK files are never modified. Native sizing is
implemented for linear formats 1-14 and 4x4 block formats 15-18. Codec and
channel mappings that are not established are preserved as raw bytes.

Known native quick-decode layouts:
  1: 32-bit texel copied unchanged (channel order unresolved; raw export)
  2: RGB565
  3: 16-bit native conversion (exact channel packing unresolved; raw export)
  4: RGBA4444
  5: A8 (white RGB with source byte as alpha)
  6: R8-style single channel (grayscale PNG is a diagnostic preview)
  15-18: 4x4 block payloads; codec identities unresolved (raw export)
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

# Native bits-per-texel table. The size routine rounds IDs 15-18 to 4x4 blocks.
FORMAT_INFO = {
    1: {"name": "FORMAT_1_32BPP_DIRECT_COPY_UNMAPPED", "kind": "linear", "bits_per_pixel": 32},
    2: {"name": "RGB565", "kind": "linear", "bits_per_pixel": 16},
    3: {"name": "FORMAT_3_16BPP_NATIVE_CONVERSION_UNMAPPED", "kind": "linear", "bits_per_pixel": 16},
    4: {"name": "RGBA4444", "kind": "linear", "bits_per_pixel": 16},
    5: {"name": "A8", "kind": "linear", "bits_per_pixel": 8},
    6: {"name": "R8", "kind": "linear", "bits_per_pixel": 8},
    7: {"name": "FORMAT_7_16BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 16},
    8: {"name": "FORMAT_8_32BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 32},
    9: {"name": "FORMAT_9_16BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 16},
    10: {"name": "FORMAT_10_16BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 16},
    11: {"name": "FORMAT_11_16BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 16},
    12: {"name": "FORMAT_12_16BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 16},
    13: {"name": "FORMAT_13_32BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 32},
    14: {"name": "FORMAT_14_24BPP_UNMAPPED", "kind": "linear", "bits_per_pixel": 24},
    15: {"name": "BLOCK_4X4_8B_FORMAT15", "kind": "block4x4", "bytes_per_block": 8},
    16: {"name": "BLOCK_4X4_16B_FORMAT16", "kind": "block4x4", "bytes_per_block": 16},
    17: {"name": "BLOCK_4X4_32B_FORMAT17", "kind": "block4x4", "bytes_per_block": 32},
    18: {"name": "BLOCK_4X4_8B_FORMAT18", "kind": "block4x4", "bytes_per_block": 8},
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
        header = raw[start:start + 16]
        if header[:4] != b"ZLIB":
            raise ValueError(f"allocation {i}: expected ZLIB header, got {header[:4]!r}")
        unpacked, header_span, header_comp = struct.unpack_from(">III", header, 4)
        if header_span != span or header_comp != comp_size:
            raise ValueError(f"allocation {i}: subheader/ZLIB size mismatch")
        block = zlib.decompress(raw[start + 16:start + span])
        if len(block) != size or len(block) != unpacked:
            raise ValueError(f"allocation {i}: decompressed length mismatch")
        banks[aid] = block

    if OBJECT_BANK_ID not in banks or PIXEL_BANK_ID not in banks:
        raise ValueError("IFF is missing the VCTEXTURE object or pixel allocation")

    relative_entries = struct.unpack_from(f"<{file_count}Q", raw, file_table)
    entries: list[dict[str, int]] = []
    for index, relative in enumerate(relative_entries):
        entry_at = file_table + relative - 1 + index * 8
        id0, id1, entry_type = struct.unpack_from("<IIQ", raw, entry_at)
        if entry_type != 2:
            continue
        object_base, pixel_base = struct.unpack_from("<QQ", raw, entry_at + 16)
        entries.append({
            "object_base": object_base, "pixel_base": pixel_base,
            "index": index, "id0": id0, "id1": id1,
        })
    if not entries:
        raise ValueError("IFF contains no paired type-2 file entries")
    entries.sort(key=lambda item: (item["object_base"], item["index"]))
    return banks[OBJECT_BANK_ID], banks[PIXEL_BANK_ID], entries


def _entry_for_descriptor(
    descriptor_offset: int, object_data: bytes, entries: list[dict[str, int]]
) -> dict[str, int]:
    starts = [e["object_base"] for e in entries]
    index = bisect.bisect_right(starts, descriptor_offset) - 1
    if index < 0:
        raise ValueError("no paired file-entry base at or before descriptor")
    entry = entries[index]
    next_base = next(
        (e["object_base"] for e in entries[index + 1:]
         if e["object_base"] > entry["object_base"]),
        None,
    )
    end = descriptor_offset + VCTEXTURE_MIN_SIZE
    if end > len(object_data):
        raise ValueError("VCTEXTURE descriptor extends beyond object allocation")
    if next_base is not None and end > next_base:
        raise ValueError("VCTEXTURE descriptor crosses the next file-entry range")
    return entry


def _payload_size(format_id: int, width: int, height: int, depth: int = 1) -> int:
    if width <= 0 or height <= 0 or depth <= 0:
        raise ValueError(f"invalid dimensions {width}x{height}x{depth}")
    info = FORMAT_INFO.get(format_id)
    if info is None:
        raise ValueError(f"unsupported VCTEXTURE format ID {format_id}")
    if info["kind"] == "linear":
        return (width * height * depth * int(info["bits_per_pixel"]) + 7) // 8
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    return blocks_x * blocks_y * depth * int(info["bytes_per_block"])


def resolve_texture_payload(
    iff_path: str | Path, descriptor_offset: int
) -> tuple[dict[str, Any], bytes]:
    object_data, pixel_data, entries = _read_iff(iff_path)
    if descriptor_offset < 0 or descriptor_offset + VCTEXTURE_MIN_SIZE > len(object_data):
        raise ValueError("descriptor offset outside object allocation")

    texture_hash = struct.unpack_from("<I", object_data, descriptor_offset)[0]
    format_id = struct.unpack_from("<I", object_data, descriptor_offset + 0x10)[0]
    width, height = struct.unpack_from("<HH", object_data, descriptor_offset + 0xC8)
    depth = max(1, object_data[descriptor_offset + 0xCC])
    mip_count = object_data[descriptor_offset + 0xCE]
    packed_flags = struct.unpack_from("<I", object_data, descriptor_offset + 0xC4)[0]
    subresource_selector = (packed_flags >> 22) & 0xF
    pointer_word = struct.unpack_from("<I", object_data, descriptor_offset + 0xD8)[0]

    if not 1 <= mip_count <= 16:
        raise ValueError(f"invalid mip count {mip_count}")
    if pointer_word == 0:
        raise ValueError("VCTEXTURE pixel pointer is null")
    info = FORMAT_INFO.get(format_id)
    if info is None:
        raise ValueError(f"unsupported VCTEXTURE format ID {format_id}")

    payload_bytes = _payload_size(format_id, width, height, depth)
    entry = _entry_for_descriptor(descriptor_offset, object_data, entries)

    # VCTexture_MakeRelative stores a valid relative pointer as offset + 1.
    relative_offset = pointer_word - 1
    pixel_offset = entry["pixel_base"] + relative_offset
    pixel_end = pixel_offset + payload_bytes
    if pixel_offset < 0 or pixel_end > len(pixel_data):
        raise ValueError(
            f"resolved pixel range [{pixel_offset:#x}, {pixel_end:#x}) "
            f"exceeds pixel allocation size {len(pixel_data):#x}"
        )

    metadata: dict[str, Any] = {
        "iff": Path(iff_path).name,
        "descriptor_offset": hex(descriptor_offset),
        "texture_hash": f"{texture_hash:08X}",
        "format_id": format_id,
        "format_name": info["name"],
        "dimensions": {"width": width, "height": height, "depth": depth},
        "mip_count": mip_count,
        "subresource_selector_field": subresource_selector,
        "exported_mip": 0,
        "packed_flags": hex(packed_flags),
        "file_entry_index": entry["index"],
        "file_entry_id0": f"{entry['id0']:08X}",
        "file_entry_id1": f"{entry['id1']:08X}",
        "object_base": hex(entry["object_base"]),
        "pixel_base": hex(entry["pixel_base"]),
        "pointer_word": hex(pointer_word),
        "relative_pixel_offset": hex(relative_offset),
        "resolved_pixel_offset": hex(pixel_offset),
        "payload_bytes": payload_bytes,
    }
    return metadata, pixel_data[pixel_offset:pixel_end]


def _expand5(value: int) -> int:
    return (value * 255 + 15) // 31


def _expand6(value: int) -> int:
    return (value * 255 + 31) // 63


def decode_rgb565(payload: bytes, width: int, height: int) -> Image.Image:
    if len(payload) != width * height * 2:
        raise ValueError("RGB565 payload length does not match dimensions")
    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        word = payload[i * 2] | (payload[i * 2 + 1] << 8)
        j = i * 4
        rgba[j:j + 4] = bytes((
            _expand5((word >> 11) & 0x1F),
            _expand6((word >> 5) & 0x3F),
            _expand5(word & 0x1F),
            255,
        ))
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def decode_rgba4444(payload: bytes, width: int, height: int) -> Image.Image:
    if len(payload) != width * height * 2:
        raise ValueError("RGBA4444 payload length does not match dimensions")
    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        word = payload[i * 2] | (payload[i * 2 + 1] << 8)
        j = i * 4
        rgba[j:j + 4] = bytes((
            ((word >> 12) & 0xF) * 17,
            ((word >> 8) & 0xF) * 17,
            ((word >> 4) & 0xF) * 17,
            (word & 0xF) * 17,
        ))
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def decode_a8(payload: bytes, width: int, height: int) -> Image.Image:
    if len(payload) != width * height:
        raise ValueError("A8 payload length does not match dimensions")
    rgba = bytearray(width * height * 4)
    for i, alpha in enumerate(payload):
        rgba[i * 4:i * 4 + 4] = bytes((255, 255, 255, alpha))
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def decode_r8_preview(payload: bytes, width: int, height: int) -> Image.Image:
    if len(payload) != width * height:
        raise ValueError("R8 payload length does not match dimensions")
    # A grayscale PNG is visualization only; the original R8 payload is raw.
    return Image.frombytes("L", (width, height), payload).convert("RGBA")


def export_texture(
    iff_path: str | Path, descriptor_offset: int, output_path: str | Path
) -> dict[str, Any]:
    metadata, payload = resolve_texture_payload(iff_path, descriptor_offset)
    output = Path(output_path)
    width = metadata["dimensions"]["width"]
    height = metadata["dimensions"]["height"]
    decoders = {
        2: (decode_rgb565, "RGB565 PNG"),
        4: (decode_rgba4444, "RGBA4444 PNG"),
        5: (decode_a8, "A8 white/alpha PNG preview"),
        6: (decode_r8_preview, "R8 grayscale PNG preview"),
    }
    if metadata["format_id"] in decoders:
        decoder, output_kind = decoders[metadata["format_id"]]
        decoder(payload, width, height).save(output)
        metadata["output_kind"] = output_kind
    else:
        # Keep unmapped linear formats and block-compressed bytes losslessly.
        output.write_bytes(payload)
        metadata["output_kind"] = "raw texture payload; decoder not implemented"

    raw_payload_path = output.with_suffix(output.suffix + ".raw.bin")
    raw_payload_path.write_bytes(payload)
    metadata["raw_payload"] = str(raw_payload_path)
    metadata["output"] = str(output)
    sidecar = output.with_suffix(output.suffix + ".json")
    sidecar.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata["sidecar"] = str(sidecar)
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iff", help="source IFF file")
    parser.add_argument("--record-offset", required=True, help="VCTEXTURE object offset, e.g. 0x1E330")
    parser.add_argument("--output", required=True, help="PNG for decoded formats, raw data for others")
    args = parser.parse_args()
    print(json.dumps(export_texture(args.iff, int(args.record_offset, 0), args.output), indent=2))
