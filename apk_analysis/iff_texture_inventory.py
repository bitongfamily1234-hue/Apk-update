#!/usr/bin/env python3
"""Inventory and optionally export VCTEXTURE resources from NBA 2K20 Mobile IFFs.

The scanner does not modify its source IFF. It resolves each texture through the
paired type-2 resource entry before examining the pixel-bank payload.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

IFF_MAGIC = bytes.fromhex("94ef3bff")
OBJECT_BANK_ID = 0xBB05A9C1
PIXEL_BANK_ID = 0x411536D5
VCTEXTURE_MIN_SIZE = 0xDC

LINEAR_BPP = {
    1: 32, 2: 16, 3: 16, 4: 16, 5: 8, 6: 8, 7: 16,
    8: 32, 9: 16, 10: 16, 11: 16, 12: 16, 13: 32, 14: 24,
}
BLOCK_BYTES = {15: 8, 16: 16, 17: 32, 18: 8}
FORMAT_NAMES = {
    1: "32bpp_native_unmapped",
    2: "RGB565",
    3: "16bpp_native_unmapped",
    4: "RGBA4444",
    5: "A8",
    6: "R8",
    7: "16bpp_native_unmapped",
    8: "32bpp_native_unmapped",
    9: "16bpp_native_unmapped",
    10: "16bpp_native_unmapped",
    11: "16bpp_native_unmapped",
    12: "16bpp_native_unmapped",
    13: "32bpp_native_unmapped",
    14: "24bpp_native_unmapped",
    15: "ETC2_RGB8",
    16: "16byte_4x4_block_unconfirmed",
    17: "32byte_4x4_block_unconfirmed",
    18: "8byte_4x4_block_unconfirmed",
}


def read_iff(path: str | Path) -> tuple[bytes, bytes, list[dict[str, int]], dict[str, Any]]:
    raw = Path(path).read_bytes()
    if raw[:4] != IFF_MAGIC:
        raise ValueError(f"{path}: unsupported IFF magic {raw[:4].hex()}")

    sub_count = struct.unpack_from("<Q", raw, 0x10)[0]
    seed = struct.unpack_from("<Q", raw, 0x18)[0]
    file_count = struct.unpack_from("<Q", raw, 0x20)[0]
    first_subhead = struct.unpack_from("<Q", raw, 0x28)[0]
    subheader_table = seed + 23
    file_table = 40 + first_subhead - 1

    banks: dict[int, bytes] = {}
    allocations: list[dict[str, int]] = []
    for index in range(sub_count):
        aid, id1, kind, size, comp_size, start, span, _reserved = struct.unpack_from(
            "<IIQQQQQQ", raw, subheader_table + index * 56
        )
        if start + span > len(raw) or span < 16:
            raise ValueError(f"allocation {index}: compressed range is outside the IFF")
        header = raw[start:start + 16]
        if header[:4] != b"ZLIB":
            raise ValueError(f"allocation {index}: expected ZLIB header, got {header[:4]!r}")
        unpacked, header_span, header_comp = struct.unpack_from(">III", header, 4)
        if header_span != span or header_comp != comp_size:
            raise ValueError(f"allocation {index}: subheader/ZLIB size mismatch")
        block = zlib.decompress(raw[start + 16:start + span])
        if len(block) != size or len(block) != unpacked:
            raise ValueError(f"allocation {index}: decompressed length mismatch")
        banks[aid] = block
        allocations.append({
            "index": index, "allocation_id": aid, "secondary_id": id1,
            "kind": kind, "uncompressed_bytes": size,
            "compressed_bytes": comp_size, "file_offset": start, "span": span,
        })

    if OBJECT_BANK_ID not in banks or PIXEL_BANK_ID not in banks:
        raise ValueError("IFF is missing the expected VCTEXTURE object or pixel allocation")
    if file_table < 0 or file_table + file_count * 8 > len(raw):
        raise ValueError("IFF file-entry offset table is outside the file")

    rel_offsets = struct.unpack_from(f"<{file_count}Q", raw, file_table)
    paired: list[dict[str, int]] = []
    type1_entries: list[dict[str, int]] = []
    type_counts: collections.Counter[str] = collections.Counter()
    for index, relative in enumerate(rel_offsets):
        entry_at = file_table + relative - 1 + index * 8
        if entry_at < 0 or entry_at + 32 > len(raw):
            raise ValueError(f"file entry {index}: header outside IFF")
        id0, id1, entry_type = struct.unpack_from("<IIQ", raw, entry_at)
        values = struct.unpack_from("<QQ", raw, entry_at + 16)
        type_counts[str(entry_type)] += 1
        if entry_type == 2:
            paired.append({
                "object_base": values[0], "pixel_base": values[1],
                "index": index, "id0": id0, "id1": id1,
            })
        elif entry_type == 1:
            type1_entries.append({
                "index": index, "id0": id0, "id1": id1,
                "object_offset": values[0], "auxiliary_word": values[1],
            })

    if not paired:
        raise ValueError("IFF contains no paired type-2 file entries")
    paired.sort(key=lambda item: (item["object_base"], item["index"]))
    object_bank = banks[OBJECT_BANK_ID]
    for item in type1_entries:
        offset = item["object_offset"]
        if 0 <= offset < len(object_bank):
            item["header_hex_32"] = object_bank[offset:offset + 32].hex()
            item["header_marker_le"] = (
                f"{struct.unpack_from('<I', object_bank, offset)[0]:08X}"
                if offset + 4 <= len(object_bank) else None
            )
            item["offset_in_object_bank_valid"] = True
        else:
            item["header_hex_32"] = ""
            item["header_marker_le"] = None
            item["offset_in_object_bank_valid"] = False

    metadata = {
        "file": Path(path).name,
        "object_bank_bytes": len(object_bank),
        "pixel_bank_bytes": len(banks[PIXEL_BANK_ID]),
        "allocation_count": len(allocations),
        "file_entry_count": int(file_count),
        "file_entry_type_counts": dict(type_counts),
        "type1_nontexture_leads": type1_entries,
        "allocations": allocations,
    }
    return banks[OBJECT_BANK_ID], banks[PIXEL_BANK_ID], paired, metadata


def payload_size(format_id: int, width: int, height: int, depth: int = 1) -> int:
    if min(width, height, depth) <= 0:
        raise ValueError("texture dimensions and depth must be positive")
    if format_id in LINEAR_BPP:
        return (width * height * depth * LINEAR_BPP[format_id] + 7) // 8
    if format_id in BLOCK_BYTES:
        return ((width + 3) // 4) * ((height + 3) // 4) * depth * BLOCK_BYTES[format_id]
    raise ValueError(f"unsupported texture format ID {format_id}")


def find_entry(
    descriptor_offset: int, object_bank: bytes, entries: list[dict[str, int]]
) -> dict[str, int] | None:
    starts = [item["object_base"] for item in entries]
    index = bisect.bisect_right(starts, descriptor_offset) - 1
    if index < 0:
        return None
    entry = entries[index]
    end = descriptor_offset + VCTEXTURE_MIN_SIZE
    if end > len(object_bank):
        return None
    next_base = next(
        (item["object_base"] for item in entries[index + 1:]
         if item["object_base"] > entry["object_base"]),
        None,
    )
    if next_base is not None and end > next_base:
        return None
    return entry


def scan_textures(
    iff_path: str | Path,
    min_dimension: int = 16,
    max_dimension: int = 4096,
    alignment: int = 16,
) -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    object_bank, pixel_bank, entries, metadata = read_iff(iff_path)
    starts = [entry["object_base"] for entry in entries]
    candidates: list[dict[str, Any]] = []

    for offset in range(0, max(0, len(object_bank) - VCTEXTURE_MIN_SIZE + 1), alignment):
        texture_hash = struct.unpack_from("<I", object_bank, offset)[0]
        format_id = struct.unpack_from("<I", object_bank, offset + 0x10)[0]
        if format_id not in LINEAR_BPP and format_id not in BLOCK_BYTES:
            continue
        width, height = struct.unpack_from("<HH", object_bank, offset + 0xC8)
        depth = max(1, object_bank[offset + 0xCC])
        mip_count = object_bank[offset + 0xCE]
        flags = struct.unpack_from("<I", object_bank, offset + 0xC4)[0]
        pointer_word = struct.unpack_from("<I", object_bank, offset + 0xD8)[0]
        if not (min_dimension <= width <= max_dimension and min_dimension <= height <= max_dimension):
            continue
        if not (1 <= depth <= 64 and 1 <= mip_count <= 16 and pointer_word != 0):
            continue

        entry_index = bisect.bisect_right(starts, offset) - 1
        if entry_index < 0:
            continue
        entry = entries[entry_index]
        descriptor_end = offset + VCTEXTURE_MIN_SIZE
        next_base = next(
            (item["object_base"] for item in entries[entry_index + 1:]
             if item["object_base"] > entry["object_base"]),
            None,
        )
        if descriptor_end > len(object_bank) or (
            next_base is not None and descriptor_end > next_base
        ):
            continue

        size = payload_size(format_id, width, height, depth)
        relative_offset = pointer_word - 1
        pixel_offset = entry["pixel_base"] + relative_offset
        if pixel_offset < 0 or pixel_offset + size > len(pixel_bank):
            continue

        candidates.append({
            "descriptor_offset": offset,
            "texture_hash": f"{texture_hash:08X}",
            "format_id": format_id,
            "format_name": FORMAT_NAMES[format_id],
            "width": width,
            "height": height,
            "depth": depth,
            "mip_count": mip_count,
            "packed_flags": f"0x{flags:08X}",
            "subresource_selector_bits_22_25": (flags >> 22) & 0xF,
            "pointer_word": f"0x{pointer_word:X}",
            "relative_pixel_offset": f"0x{relative_offset:X}",
            "pixel_offset": pixel_offset,
            "payload_bytes_mip0": size,
            "file_entry_index": entry["index"],
            "file_entry_id0": f"{entry['id0']:08X}",
            "file_entry_id1": f"{entry['id1']:08X}",
            "object_base": f"0x{entry['object_base']:X}",
            "pixel_base": f"0x{entry['pixel_base']:X}",
        })

    metadata["candidate_count"] = len(candidates)
    metadata["format_histogram"] = {
        str(key): value for key, value in sorted(
            collections.Counter(item["format_id"] for item in candidates).items()
        )
    }
    metadata["mip_histogram"] = {
        str(key): value for key, value in sorted(
            collections.Counter(item["mip_count"] for item in candidates).items()
        )
    }
    metadata["depth_histogram"] = {
        str(key): value for key, value in sorted(
            collections.Counter(item["depth"] for item in candidates).items()
        )
    }
    return metadata, candidates, pixel_bank


def _decode_preview(data: bytes, format_id: int, width: int, height: int) -> Image.Image:
    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        target = index * 4
        if format_id == 2:
            word = data[index * 2] | (data[index * 2 + 1] << 8)
            r, g, b = (word >> 11) & 31, (word >> 5) & 63, word & 31
            rgba[target:target + 4] = bytes((
                (r * 255 + 15) // 31, (g * 255 + 31) // 63,
                (b * 255 + 15) // 31, 255,
            ))
        elif format_id == 4:
            word = data[index * 2] | (data[index * 2 + 1] << 8)
            rgba[target:target + 4] = bytes((
                ((word >> 12) & 15) * 17, ((word >> 8) & 15) * 17,
                ((word >> 4) & 15) * 17, (word & 15) * 17,
            ))
        elif format_id == 5:
            rgba[target:target + 4] = bytes((255, 255, 255, data[index]))
        elif format_id == 6:
            value = data[index]
            rgba[target:target + 4] = bytes((value, value, value, 255))
        else:
            raise ValueError(f"no PNG preview decoder for format {format_id}")
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def export_candidates(
    iff_path: str | Path,
    metadata: dict[str, Any],
    candidates: list[dict[str, Any]],
    pixel_bank: bytes,
    output_dir: str | Path,
    export_raw: bool = True,
) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    preview_items: dict[int, list[tuple[dict[str, Any], Image.Image]]] = collections.defaultdict(list)
    for record in candidates:
        start = record["pixel_offset"]
        end = start + record["payload_bytes_mip0"]
        payload = pixel_bank[start:end]
        stem = (
            f'{Path(iff_path).stem}_{record["descriptor_offset"]:06X}_'
            f'{record["texture_hash"]}_f{record["format_id"]}_'
            f'{record["width"]}x{record["height"]}'
        )
        if export_raw:
            raw_path = out / f"{stem}.raw.bin"
            raw_path.write_bytes(payload)
            record["raw_payload_file"] = raw_path.name
        if record["format_id"] in (2, 4, 5, 6) and record["depth"] == 1:
            try:
                image = _decode_preview(
                    payload, record["format_id"], record["width"], record["height"]
                )
                preview_path = out / f"{stem}.png"
                image.save(preview_path)
                record["preview_file"] = preview_path.name
                image.thumbnail((160, 120))
                preview_items[record["format_id"]].append((record, image.copy()))
            except (ValueError, IndexError) as error:
                record["preview_error"] = str(error)

    for format_id, items in preview_items.items():
        columns, cell_w, cell_h = 5, 210, 165
        rows = math.ceil(len(items) / columns)
        sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), (35, 35, 35))
        draw = ImageDraw.Draw(sheet)
        for index, (record, image) in enumerate(items):
            x, y = (index % columns) * cell_w, (index // columns) * cell_h
            sheet.paste(image, (x + (160 - image.width) // 2, y))
            draw.text((x + 5, y + 122), f'{record["texture_hash"]} @ 0x{record["descriptor_offset"]:X}', fill="white")
            draw.text((x + 5, y + 138), f'{record["width"]}x{record["height"]} f{format_id}', fill="white")
        sheet.save(out / f'{Path(iff_path).stem}_format{format_id}_contact.png')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iffs", nargs="+", help="IFF files to inventory")
    parser.add_argument("--out", default="iff_texture_inventory", help="output folder")
    parser.add_argument("--export-raw", action="store_true", help="write exact mip-0 payloads beside previews")
    parser.add_argument("--min-dimension", type=int, default=16)
    parser.add_argument("--max-dimension", type=int, default=4096)
    parser.add_argument("--alignment", type=int, default=16)
    args = parser.parse_args()
    if args.alignment <= 0:
        parser.error("--alignment must be positive")

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    for iff_path in args.iffs:
        metadata, candidates, pixel_bank = scan_textures(
            iff_path, args.min_dimension, args.max_dimension, args.alignment
        )
        (output / f"{Path(iff_path).stem}_inventory.json").write_text(
            json.dumps({"metadata": metadata, "textures": candidates}, indent=2) + "\n",
            encoding="utf-8",
        )
        export_candidates(iff_path, metadata, candidates, pixel_bank, output, args.export_raw)
        print(
            f'{metadata["file"]}: {metadata["candidate_count"]} candidates; '
            f'formats={metadata["format_histogram"]}; '
            f'mips={metadata["mip_histogram"]}'
        )


if __name__ == "__main__":
    main()
