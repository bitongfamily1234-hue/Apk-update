"""Guarded native-layout correction for NBA 2K20 Mobile IFFs.

Installed by a small wrapper around the original iffengine.core bytecode.
The original engine remains responsible for session management, rendering,
PNG I/O, and rebuilding the compressed IFF. This module only corrects the
native VCTEXTURE size/channel interpretation and per-file paired pixel base.
"""
from __future__ import annotations

import bisect
import struct
import threading
from dataclasses import dataclass
from typing import Any

MAGIC = bytes.fromhex("94ef3bff")
OBJECT_ID = 0xBB05A9C1
PIXEL_ID = 0x411536D5
HEADER_SPAN = 0xDC
NATIVE_BPP = {
    1: 32, 2: 16, 3: 16, 4: 16, 5: 8, 6: 8, 7: 16,
    8: 32, 9: 16, 10: 16, 11: 16, 12: 16, 13: 32, 14: 24,
}
NATIVE_BLOCK_BYTES = {15: 8, 16: 16, 17: 32, 18: 8}
SCAN_FORMATS = (1, 2, 4, 5, 6, 7, 15, 16)
SCAN_DIMENSIONS = {4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384}


@dataclass(frozen=True)
class Allocation:
    index: int
    allocation_id: int
    raw_position: int
    unpacked_size: int
    stored_size: int
    span: int


@dataclass(frozen=True)
class PairedEntry:
    index: int
    object_base: int
    pixel_base: int
    id0: int
    id1: int


@dataclass(frozen=True)
class Layout:
    allocations: tuple[Allocation, ...]
    object_global_base: int
    object_size: int
    pixel_global_base: int
    pixel_size: int
    entries: tuple[PairedEntry, ...]


def native_payload_size(format_id: int, width: int, height: int, depth: int = 1) -> int:
    if min(width, height, depth) <= 0:
        raise ValueError(f"invalid native texture dimensions {width}x{height}x{depth}")
    if format_id in NATIVE_BPP:
        return (width * height * depth * NATIVE_BPP[format_id] + 7) // 8
    if format_id in NATIVE_BLOCK_BYTES:
        return ((width + 3) // 4) * ((height + 3) // 4) * depth * NATIVE_BLOCK_BYTES[format_id]
    raise ValueError(f"unsupported native texture format {format_id}")


def parse_layout(raw: bytes, block_sizes, wrapper_blocks=None) -> Layout:
    if len(raw) < 0x30 or raw[:4] != MAGIC:
        raise ValueError("not a mobile compressed IFF")
    sub_count = struct.unpack_from("<Q", raw, 0x10)[0]
    seed = struct.unpack_from("<Q", raw, 0x18)[0]
    file_count = struct.unpack_from("<Q", raw, 0x20)[0]
    first_subhead = struct.unpack_from("<Q", raw, 0x28)[0]
    if not 1 <= sub_count <= 4096:
        raise ValueError(f"invalid mobile IFF allocation count {sub_count}")
    sub_table = seed + 23
    file_table = 40 + first_subhead - 1
    if sub_table < 0 or sub_table + sub_count * 56 > len(raw):
        raise ValueError("mobile IFF allocation table is outside the file")
    if file_table < 0 or file_count > 2_000_000 or file_table + file_count * 8 > len(raw):
        raise ValueError("mobile IFF file-entry table is outside the file")
    if len(block_sizes) != sub_count:
        raise ValueError("decompressed allocation count differs from the raw subheader count")
    if wrapper_blocks is not None and len(wrapper_blocks) != sub_count:
        raise ValueError("decompressor metadata count differs from the raw subheader count")

    allocations = []
    for i in range(sub_count):
        _aid0, _aid1, _kind, unpacked, stored, position, span, _reserved = struct.unpack_from(
            "<IIQQQQQQ", raw, sub_table + i * 56
        )
        if unpacked <= 0 or span < 16 or position + span > len(raw):
            raise ValueError(f"invalid allocation descriptor {i}")
        if raw[position:position + 4] != b"ZLIB":
            raise ValueError(f"allocation descriptor {i} does not point to a ZLIB header")
        z_unpacked, z_span, z_stored = struct.unpack_from(">III", raw, position + 4)
        if (unpacked, span, stored) != (z_unpacked, z_span, z_stored):
            raise ValueError(f"allocation descriptor {i} disagrees with its ZLIB header")
        if int(block_sizes[i]) != unpacked:
            raise ValueError(f"allocation {i} decompressed size mismatch")
        if wrapper_blocks is not None and int(wrapper_blocks[i].position) != position:
            raise ValueError(f"allocation {i} order differs from the decompressor's block order")
        aid = struct.unpack_from("<I", raw, sub_table + i * 56)[0]
        allocations.append(Allocation(i, aid, position, unpacked, stored, span))

    ids = [a.allocation_id for a in allocations]
    if ids.count(OBJECT_ID) != 1 or ids.count(PIXEL_ID) != 1:
        raise ValueError("required object/pixel allocation IDs were not found exactly once")
    object_index = ids.index(OBJECT_ID)
    pixel_index = ids.index(PIXEL_ID)
    bases = []
    cursor = 0
    for size in block_sizes:
        bases.append(cursor)
        cursor += int(size)

    rels = struct.unpack_from(f"<{file_count}Q", raw, file_table)
    entries = []
    for index, relative in enumerate(rels):
        if relative == 0:
            raise ValueError(f"file-entry offset {index} is null")
        entry_at = file_table + relative - 1 + index * 8
        if entry_at < 0 or entry_at + 32 > len(raw):
            raise ValueError(f"file-entry record {index} is outside the file")
        id0, id1, entry_type = struct.unpack_from("<IIQ", raw, entry_at)
        if entry_type != 2:
            continue
        object_base, pixel_base = struct.unpack_from("<QQ", raw, entry_at + 16)
        if object_base >= allocations[object_index].unpacked_size:
            raise ValueError(f"type-2 entry {index} object base is outside the object allocation")
        if pixel_base >= allocations[pixel_index].unpacked_size:
            raise ValueError(f"type-2 entry {index} pixel base is outside the pixel allocation")
        entries.append(PairedEntry(index, object_base, pixel_base, id0, id1))
    if not entries:
        raise ValueError("no paired type-2 file entries found")
    entries.sort(key=lambda item: (item.object_base, item.index))
    return Layout(
        tuple(allocations), bases[object_index], allocations[object_index].unpacked_size,
        bases[pixel_index], allocations[pixel_index].unpacked_size, tuple(entries),
    )


def _descriptor_valid(object_data: bytes, offset: int) -> bool:
    if offset < 0 or offset + HEADER_SPAN > len(object_data):
        return False
    fmt = struct.unpack_from("<I", object_data, offset + 0x10)[0]
    if fmt not in SCAN_FORMATS or struct.unpack_from("<I", object_data, offset + 0x14)[0] != fmt:
        return False
    marker = struct.unpack_from("<I", object_data, offset + 0x28)[0]
    if marker != 32 and (marker not in (40, 0) or struct.unpack_from("<I", object_data, offset + 0x2C)[0] != fmt):
        return False
    width, height = struct.unpack_from("<HH", object_data, offset + 0xC8)
    pointer_word = struct.unpack_from("<I", object_data, offset + 0xD8)[0]
    return width in SCAN_DIMENSIONS and height in SCAN_DIMENSIONS and pointer_word >= 1


def has_native_vctextures(decompressed: bytes, layout: Layout) -> bool:
    start = layout.object_global_base
    object_data = decompressed[start:start + layout.object_size]
    for marker_value in (32, 40):
        marker = struct.pack("<I", marker_value)
        pos = 40
        while True:
            found = object_data.find(marker, pos)
            if found < 0:
                break
            pos = found + 1
            descriptor = found - 0x28
            if descriptor % 4 == 0 and _descriptor_valid(object_data, descriptor):
                return True
    return False


def _resolve(layout: Layout, object_local: int, pointer_word: int,
             format_id: int, width: int, height: int, depth: int) -> dict[str, int]:
    if object_local < 0 or object_local + HEADER_SPAN > layout.object_size:
        raise ValueError("VCTEXTURE descriptor is outside the object allocation")
    starts = [entry.object_base for entry in layout.entries]
    index = bisect.bisect_right(starts, object_local) - 1
    if index < 0:
        raise ValueError("no paired type-2 entry owns this VCTEXTURE")
    entry = layout.entries[index]
    next_base = next(
        (item.object_base for item in layout.entries[index + 1:] if item.object_base > entry.object_base),
        None,
    )
    if next_base is not None and object_local + HEADER_SPAN > next_base:
        raise ValueError("VCTEXTURE descriptor crosses into the next paired object range")
    relative = pointer_word - 1
    if relative < 0:
        raise ValueError("invalid encoded VCTEXTURE pointer")
    size = native_payload_size(format_id, width, height, depth)
    local_pixel = entry.pixel_base + relative
    if local_pixel + size > layout.pixel_size:
        raise ValueError("resolved VCTEXTURE payload exceeds the pixel allocation")
    return {
        "entry_index": entry.index,
        "pixel_base": entry.pixel_base,
        "pixel_local": local_pixel,
        "pixel_global": layout.pixel_global_base + local_pixel,
        "payload_size": size,
        "depth": depth,
    }


def apply_layout(opened, raw: bytes, layout: Layout) -> dict[str, Any]:
    data = opened.data
    changed = []
    skipped = []
    failures = []
    for texture in opened.textures:
        if not getattr(texture, "has_header", True) or texture.payload_bytes is not None:
            skipped.append(getattr(texture, "number", None))
            continue
        absolute = int(texture.header_offset)
        if not layout.object_global_base <= absolute < layout.object_global_base + layout.object_size:
            skipped.append(getattr(texture, "number", None))
            continue
        local = absolute - layout.object_global_base
        if local + 0xDC > layout.object_size:
            skipped.append(getattr(texture, "number", None))
            continue
        # Confirm this is the standard 2K20 VCTEXTURE record, not a compact
        # 2K16 header or a scene/resource record that resembles a texture.
        fmt = struct.unpack_from("<I", data, absolute + 0x10)[0]
        width, height = struct.unpack_from("<HH", data, absolute + 0xC8)
        marker = struct.unpack_from("<I", data, absolute + 0x28)[0]
        pointer_word = struct.unpack_from("<I", data, absolute + 0xD8)[0]
        if (
            fmt != int(texture.format_id)
            or struct.unpack_from("<I", data, absolute + 0x14)[0] != fmt
            or marker not in (0, 32, 40)
            or (marker != 32 and struct.unpack_from("<I", data, absolute + 0x2C)[0] != fmt)
            or width != int(texture.width)
            or height != int(texture.height)
            or pointer_word < 1
        ):
            skipped.append(getattr(texture, "number", None))
            continue
        depth = max(1, int(data[absolute + 0xCC]))
        native_mips = max(1, int(data[absolute + 0xCE]))
        try:
            resolved = _resolve(layout, local, pointer_word, fmt, width, height, depth)
            texture.payload_offset = resolved["pixel_global"]
            texture.payload_size = resolved["payload_size"]
            texture.tagged_offset = pointer_word - 1
            texture.location = (
                f"type-2 entry {resolved['entry_index']} / pixel allocation "
                f"+ 0x{resolved['pixel_local']:X}"
            )
            texture.error = None
            texture.status = "valid"
            texture.uses_mip_chain = False
            texture.mip_count = 1
            texture.encoded_size = texture.payload_size
            texture._mobile_native = True
            texture._mobile_native_depth = depth
            texture._mobile_native_mips = native_mips
            changed.append({
                "number": texture.number,
                "header_offset": absolute,
                "format_id": fmt,
                "width": width,
                "height": height,
                "depth": depth,
                "native_mip_count": native_mips,
                **resolved,
            })
        except Exception as exc:
            texture.payload_offset = None
            texture.error = f"mobile IFF paired-entry resolution failed: {exc}"
            texture.status = "unresolved"
            texture._mobile_native = True
            failures.append({"number": texture.number, "error": str(exc)})

    targets = [
        t for t in opened.textures
        if getattr(t, "_mobile_native", False) and t.payload_offset is not None
    ]
    starts = sorted({int(t.payload_offset) for t in targets})
    pixel_end = layout.pixel_global_base + layout.pixel_size
    for texture in targets:
        next_start = next((value for value in starts if value > texture.payload_offset), pixel_end)
        texture.allocation_size = max(0, next_start - texture.payload_offset)
        if texture.allocation_size < texture.payload_size:
            texture.payload_offset = None
            texture.error = "mobile IFF texture overlaps the next resolved payload; exporting/replacement disabled"
            texture.status = "unresolved"
            failures.append({"number": texture.number, "error": texture.error})

    report = {
        "format": "NBA 2K20 Mobile paired-entry IFF",
        "object_block_index": next(a.index for a in layout.allocations if a.allocation_id == OBJECT_ID),
        "pixel_block_index": next(a.index for a in layout.allocations if a.allocation_id == PIXEL_ID),
        "object_global_base": layout.object_global_base,
        "pixel_global_base": layout.pixel_global_base,
        "object_size": layout.object_size,
        "pixel_size": layout.pixel_size,
        "texture_count_remapped": len(changed) - len(failures),
        "texture_count_skipped": len(skipped),
        "failures": failures,
        "textures": changed,
    }
    opened._mobile_native_layout_report = report
    return report


def _decode_a8(payload: bytes, width: int, height: int) -> bytes:
    if len(payload) != width * height:
        raise ValueError("A8 byte count does not match dimensions")
    output = bytearray(width * height * 4)
    for index, alpha in enumerate(payload):
        offset = index * 4
        output[offset:offset + 4] = bytes((255, 255, 255, alpha))
    return bytes(output)


def _decode_r8_preview(payload: bytes, width: int, height: int) -> bytes:
    if len(payload) != width * height:
        raise ValueError("R8 byte count does not match dimensions")
    output = bytearray(width * height * 4)
    for index, value in enumerate(payload):
        offset = index * 4
        output[offset:offset + 4] = bytes((value, value, value, 255))
    return bytes(output)


def _enter_mobile(namespace, active: bool):
    context = namespace["_mobile_iff_patch_context"]
    previous = getattr(context, "active", False)
    context.active = active
    previous_etc = namespace.get("FORMAT_ETC1_RGB", 6)
    previous_formats = namespace.get("FORMATS", ())
    if active:
        namespace["FORMAT_ETC1_RGB"] = 15
        namespace["FORMATS"] = tuple(sorted(set(previous_formats) | {5}))
    return previous, previous_etc, previous_formats


def _leave_mobile(namespace, token):
    previous, previous_etc, previous_formats = token
    namespace["FORMAT_ETC1_RGB"] = previous_etc
    namespace["FORMATS"] = previous_formats
    namespace["_mobile_iff_patch_context"].active = previous


def install(namespace: dict[str, Any]) -> None:
    """Install guarded wrappers into the iffengine.core module namespace."""
    if namespace.get("_mobile_iff_native_patch_installed"):
        return
    original_decompress = namespace["decompress_wrapper"]
    original_open = namespace["open_iff_bytes"]
    original_payload_size = namespace["payload_size"]
    original_decode = namespace["decode_texture_rgba"]
    original_encode_level = namespace["encode_level"]
    original_import = namespace["import_png_bytes"]
    texture_record_class = namespace["TextureRecord"]
    original_as_dict = texture_record_class.as_dict
    context = threading.local()
    namespace["_mobile_iff_patch_context"] = context

    def patched_payload_size(format_id, width, height):
        if getattr(context, "active", False):
            return native_payload_size(int(format_id), int(width), int(height))
        return original_payload_size(format_id, width, height)

    def patched_decode(opened, texture):
        if getattr(texture, "_mobile_native", False) and int(texture.format_id) in (5, 6):
            if int(getattr(texture, "_mobile_native_depth", 1)) != 1:
                raise ValueError("3D/depth textures are currently exported as raw payload only")
            payload = namespace["texture_payload_bytes"](opened, texture)
            if texture.format_id == 5:
                return _decode_a8(payload, texture.width, texture.height)
            return _decode_r8_preview(payload, texture.width, texture.height)
        return original_decode(opened, texture)

    def patched_encode_level(rgba, format_id, width, height):
        if getattr(context, "active", False) and format_id in (5, 6):
            if len(rgba) < width * height * 4:
                raise ValueError("RGBA input is shorter than the target texture dimensions")
            channel = 3 if format_id == 5 else 0
            return bytes(rgba[index * 4 + channel] for index in range(width * height))
        return original_encode_level(rgba, format_id, width, height)

    def patched_import(opened, texture, png_bytes):
        if not getattr(texture, "_mobile_native", False):
            return original_import(opened, texture, png_bytes)
        token = _enter_mobile(namespace, True)
        try:
            return original_import(opened, texture, png_bytes)
        finally:
            _leave_mobile(namespace, token)

    def patched_as_dict(texture):
        result = original_as_dict(texture)
        if getattr(texture, "_mobile_native", False):
            result["format"] = {5: "A8", 6: "R8"}.get(texture.format_id, result.get("format"))
        return result

    def patched_open(raw, display_path):
        if not isinstance(raw, (bytes, bytearray)) or bytes(raw[:4]) != MAGIC:
            return original_open(raw, display_path)
        raw_bytes = bytes(raw)
        try:
            predecoded = original_decompress(raw_bytes)
            pre_data, pre_sizes, pre_wrapper, pre_info = predecoded
            if pre_wrapper != "2K compressed IFF":
                return original_open(raw, display_path)
            layout = parse_layout(raw_bytes, pre_sizes, pre_info.blocks if pre_info is not None else None)
            if not has_native_vctextures(pre_data, layout):
                return original_open(raw, display_path)
        except Exception:
            # Other generations and nonstandard IFF wrappers retain the
            # original engine path unchanged.
            return original_open(raw, display_path)

        token = _enter_mobile(namespace, True)
        old_decompress = namespace["decompress_wrapper"]
        namespace["decompress_wrapper"] = (
            lambda candidate: predecoded if candidate is raw or candidate is raw_bytes
            else original_decompress(candidate)
        )
        try:
            opened = original_open(raw_bytes, display_path)
        finally:
            namespace["decompress_wrapper"] = old_decompress
            _leave_mobile(namespace, token)

        try:
            # The native pointer map is applied only to standard 2K20
            # VCTEXTURE descriptors; 2K16 compact headers are skipped.
            apply_layout(opened, raw_bytes, layout)
        except Exception as exc:
            opened._mobile_native_layout_error = str(exc)
        return opened

    namespace["payload_size"] = patched_payload_size
    namespace["decode_texture_rgba"] = patched_decode
    namespace["encode_level"] = patched_encode_level
    namespace["import_png_bytes"] = patched_import
    texture_record_class.as_dict = patched_as_dict
    namespace["open_iff_bytes"] = patched_open
    namespace["_mobile_iff_native_patch_installed"] = True
