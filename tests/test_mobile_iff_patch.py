from types import SimpleNamespace

from apk_analysis.mobile_iff_patch import (
    Allocation,
    Layout,
    PairedEntry,
    _decode_a8,
    _decode_r8_preview,
    apply_layout,
    native_payload_size,
)


def descriptor_buffer(fmt=6, width=8, height=4, pointer=0x21):
    data = bytearray(0x600)
    off = 0x100
    data[off + 0x10:off + 0x14] = fmt.to_bytes(4, "little")
    data[off + 0x14:off + 0x18] = fmt.to_bytes(4, "little")
    data[off + 0x28:off + 0x2C] = (32).to_bytes(4, "little")
    data[off + 0xC8:off + 0xCA] = width.to_bytes(2, "little")
    data[off + 0xCA:off + 0xCC] = height.to_bytes(2, "little")
    data[off + 0xCC] = 1
    data[off + 0xCE] = 1
    data[off + 0xD8:off + 0xDC] = pointer.to_bytes(4, "little")
    return data, off


def test_native_format_sizes_match_mobile_texture_layout():
    assert native_payload_size(2, 8, 4) == 64
    assert native_payload_size(4, 8, 4) == 64
    assert native_payload_size(5, 8, 4) == 32
    assert native_payload_size(6, 8, 4) == 32
    assert native_payload_size(15, 5, 5) == 32
    assert native_payload_size(16, 5, 5) == 64
    assert native_payload_size(17, 5, 5) == 128
    assert native_payload_size(18, 5, 5) == 32


def test_r8_and_a8_visualizations():
    assert _decode_r8_preview(bytes((0x80,)), 1, 1) == bytes((128, 128, 128, 255))
    assert _decode_a8(bytes((0x80,)), 1, 1) == bytes((255, 255, 255, 128))


def test_apply_layout_resolves_paired_pixel_base_not_global_bank_zero():
    data, off = descriptor_buffer(fmt=6, width=8, height=4, pointer=0x21)
    # Concatenated data: object allocation [0, 0x300), pixel allocation starts
    # at 0x300. The paired type-2 entry adds a further 0x40 pixel-bank base.
    texture = SimpleNamespace(
        number=7, has_header=True, payload_bytes=None, header_offset=off,
        format_id=6, width=8, height=4, tagged_offset=0x20,
        payload_offset=0x301, payload_size=16, allocation_size=0,
        location="old heuristic location", error="old error", status="unresolved",
        uses_mip_chain=True, mip_count=4, encoded_size=0,
    )
    opened = SimpleNamespace(data=data, textures=[texture])
    layout = Layout(
        allocations=(
            Allocation(0, 0xBB05A9C1, 0x100, 0x300, 0x80, 0x90),
            Allocation(1, 0x411536D5, 0x200, 0x400, 0x80, 0x90),
        ),
        object_global_base=0,
        object_size=0x300,
        pixel_global_base=0x300,
        pixel_size=0x400,
        entries=(PairedEntry(12, 0, 0x40, 1, 2),),
    )
    report = apply_layout(opened, bytes(data), layout)
    assert texture.payload_offset == 0x300 + 0x40 + 0x20
    assert texture.payload_size == 8 * 4
    assert texture.allocation_size == 0x400 - 0x60
    assert texture.location == "type-2 entry 12 / pixel allocation + 0x60"
    assert texture.error is None
    assert texture.status == "valid"
    assert texture.uses_mip_chain is False
    assert texture._mobile_native is True
    assert report["texture_count_remapped"] == 1


def test_descriptor_crossing_next_object_range_is_not_resolved():
    data, off = descriptor_buffer(fmt=6, width=8, height=4, pointer=0x21)
    texture = SimpleNamespace(
        number=1, has_header=True, payload_bytes=None, header_offset=off,
        format_id=6, width=8, height=4, tagged_offset=0x20,
        payload_offset=1, payload_size=16, allocation_size=0,
        location="", error=None, status="valid", uses_mip_chain=False,
        mip_count=1, encoded_size=16,
    )
    layout = Layout(
        allocations=(
            Allocation(0, 0xBB05A9C1, 0, 0x300, 0x80, 0x90),
            Allocation(1, 0x411536D5, 0x100, 0x400, 0x80, 0x90),
        ),
        object_global_base=0, object_size=0x300,
        pixel_global_base=0x300, pixel_size=0x400,
        entries=(
            PairedEntry(1, 0x80, 0x40, 0, 0),
            PairedEntry(2, 0x150, 0x100, 0, 0),
        ),
    )
    opened = SimpleNamespace(data=data, textures=[texture])
    report = apply_layout(opened, bytes(data), layout)
    assert texture.payload_offset is None
    assert texture.status == "unresolved"
    assert "crosses into the next paired object range" in texture.error
    assert report["failures"][0]["number"] == 1
