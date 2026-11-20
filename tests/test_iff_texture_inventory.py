from apk_analysis.iff_texture_inventory import payload_size, _decode_preview


def test_linear_sizes_match_native_bits_per_texel():
    assert payload_size(2, 3, 5) == 3 * 5 * 2
    assert payload_size(4, 3, 5) == 3 * 5 * 2
    assert payload_size(5, 7, 9) == 7 * 9
    assert payload_size(14, 7, 9) == 7 * 9 * 3


def test_block_sizes_round_up_partial_4x4_tiles():
    assert payload_size(15, 5, 5) == 4 * 8
    assert payload_size(16, 5, 5) == 4 * 16
    assert payload_size(17, 5, 5) == 4 * 32
    assert payload_size(18, 5, 5) == 4 * 8


def test_block_sizes_for_exact_64_square():
    assert payload_size(15, 64, 64) == 16 * 16 * 8
    assert payload_size(16, 64, 64) == 16 * 16 * 16


def test_rgb565_preview_decodes_white():
    image = _decode_preview(bytes((0xFF, 0xFF)), 2, 1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255, 255)


def test_rgba4444_preview_decodes_opaque_red():
    image = _decode_preview(bytes((0x0F, 0xF0)), 4, 1, 1)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)


def test_a8_and_r8_are_visual_previews():
    a8 = _decode_preview(bytes((0x80,)), 5, 1, 1)
    r8 = _decode_preview(bytes((0x80,)), 6, 1, 1)
    assert a8.getpixel((0, 0)) == (255, 255, 255, 128)
    assert r8.getpixel((0, 0)) == (128, 128, 128, 255)
