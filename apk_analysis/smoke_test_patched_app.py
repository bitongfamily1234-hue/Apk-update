#!/usr/bin/env python3
"""Smoke-test the patched app.imy using the actual Python 3.11 runtime modules."""
from __future__ import annotations

import importlib
import os
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path


def make_block(data: bytes) -> bytes:
    packed = zlib.compress(data, 9)
    span = 16 + len(packed)
    # Mobile IFF ZLIB header: uncompressed bytes, total span, flags.
    return b"ZLIB" + struct.pack(">III", len(data), span, 5) + packed


def make_mobile_iff(format_id: int, width: int, height: int, pixels: bytes) -> bytes:
    object_size = 0x300
    pixel_size = 0x400
    object_data = bytearray(object_size)
    descriptor = 0x100
    struct.pack_into("<I", object_data, descriptor, 0x12345678)
    struct.pack_into("<I", object_data, descriptor + 0x10, format_id)
    struct.pack_into("<I", object_data, descriptor + 0x14, format_id)
    struct.pack_into("<I", object_data, descriptor + 0x28, 32)
    struct.pack_into("<HH", object_data, descriptor + 0xC8, width, height)
    object_data[descriptor + 0xCC] = 1
    object_data[descriptor + 0xCE] = 1
    struct.pack_into("<I", object_data, descriptor + 0xD8, 0x21)  # relative byte offset 0x20 + 1
    pixel_data = bytearray(pixel_size)
    pixel_data[0x60:0x60 + len(pixels)] = pixels

    block0 = make_block(bytes(object_data))
    block1 = make_block(bytes(pixel_data))
    prefix_size = 0xD0
    block0_pos = prefix_size
    block1_pos = block0_pos + len(block0)

    raw = bytearray(prefix_size)
    raw[:4] = bytes.fromhex("94ef3bff")
    struct.pack_into("<I", raw, 4, block0_pos)
    struct.pack_into("<Q", raw, 0x10, 2)     # allocation count
    struct.pack_into("<Q", raw, 0x18, 0x19) # allocation table begins at 0x30
    struct.pack_into("<Q", raw, 0x20, 1)    # one file entry
    struct.pack_into("<Q", raw, 0x28, 0x79)# file offset table begins at 0xA0

    # Subheader zero: VCTEXTURE object bank.
    struct.pack_into(
        "<IIQQQQQQ", raw, 0x30,
        0xBB05A9C1, 0xBB05A9C1, 0x80, object_size, 5,
        block0_pos, len(block0), 0,
    )
    # Subheader one: texture pixel bank.
    struct.pack_into(
        "<IIQQQQQQ", raw, 0x68,
        0x411536D5, 0x411536D5, 0x80, pixel_size, 5,
        block1_pos, len(block1), 0,
    )

    # File-entry relative offset array and the paired type-2 entry.
    struct.pack_into("<Q", raw, 0xA0, 9)  # entry starts at 0xA8
    struct.pack_into("<IIQ", raw, 0xA8, 0x11111111, 0x22222222, 2)
    struct.pack_into("<QQ", raw, 0xB8, 0, 0x40)  # object_base, pixel_base

    return bytes(raw) + block0 + block1


def main() -> None:
    archive = Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="patched-app-imy-") as temp:
        root = Path(temp) / "python"
        root.mkdir()
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(root)
        sys.path.insert(0, str(root))
        try:
            core = importlib.import_module("iffengine.core")
            assert core.__dict__.get("_mobile_iff_native_patch_installed") is True

            pixel_pattern = bytes((i * 7) & 0xFF for i in range(32))
            raw = make_mobile_iff(6, 8, 4, pixel_pattern)
            opened = core.open_iff_bytes(raw, Path(temp) / "fmt6.iff")
            texture = next(t for t in opened.textures if t.format_id == 6)
            expected_global = 0x300 + 0x40 + 0x20
            assert texture.payload_offset == expected_global, (
                f"paired-entry offset mismatch: got {texture.payload_offset:#x}, "
                f"expected {expected_global:#x}"
            )
            assert texture.payload_size == 8 * 4, texture.payload_size
            assert texture._mobile_native is True
            expected_rgba = bytearray()
            for value in pixel_pattern:
                expected_rgba.extend((value, value, value, 255))
            assert core.decode_texture_rgba(opened, texture) == bytes(expected_rgba)

            replacement_rgba = bytes((0x5A, 0x12, 0x34, 0xFF) * 32)
            png = core.pngio.write_png_rgba(8, 4, replacement_rgba)
            import_result = core.import_png_bytes(opened, texture, png)
            assert import_result["encoded_size"] == 32
            assert bytes(opened.data[expected_global:expected_global + 32]) == bytes((0x5A,)) * 32

            # Save/reopen verifies that the original compressed-IFF writer still
            # rebuilds the wrapper and preserves the changed pixel allocation.
            saved_path = Path(temp) / "roundtrip.iff"
            save_result = core.save_iff_copy(opened, saved_path)
            reopened_data, reopened_sizes, _, _ = core.decompress_wrapper(saved_path.read_bytes())
            assert reopened_data == bytes(opened.data)
            assert reopened_sizes == opened.block_sizes

            alpha_pattern = bytes((i * 5) & 0xFF for i in range(32))
            a8_raw = make_mobile_iff(5, 8, 4, alpha_pattern)
            a8_opened = core.open_iff_bytes(a8_raw, Path(temp) / "fmt5.iff")
            a8_texture = next(t for t in a8_opened.textures if t.format_id == 5)
            a8_rgba = core.decode_texture_rgba(a8_opened, a8_texture)
            expected_a8 = bytearray()
            for alpha in alpha_pattern:
                expected_a8.extend((255, 255, 255, alpha))
            assert a8_rgba == bytes(expected_a8)

            print({
                "patch_installed": True,
                "format6_paired_pixel_offset": hex(texture.payload_offset),
                "format6_payload_bytes": texture.payload_size,
                "format6_png_decode": "PASS",
                "format6_png_replace": "PASS",
                "compressed_iff_save_roundtrip": "PASS",
                "format5_a8_decode": "PASS",
                "saved_output": save_result["output"],
                "reopened_block_sizes": reopened_sizes,
            })
        finally:
            sys.path.remove(str(root))


if __name__ == "__main__":
    main()
