from apk_analysis.iff_texture_resolver import _payload_size, decode_rgba4444


def test_linear_rgba4444_size():
    assert _payload_size(4, 3, 5) == 3 * 5 * 2


def test_etc2_rgb_block_size_with_partial_edge_blocks():
    # A 5x5 image occupies four complete 4x4 blocks.
    assert _payload_size(15, 5, 5) == 4 * 8


def test_etc2_rgba_block_size_with_partial_edge_blocks():
    assert _payload_size(16, 5, 5) == 4 * 16


def test_etc2_exact_block_dimensions():
    assert _payload_size(15, 64, 64) == 16 * 16 * 8
    assert _payload_size(16, 64, 64) == 16 * 16 * 16


def test_rgba4444_channel_expansion():
    # Little-endian packed word 0xF00F expands to opaque red.
    image = decode_rgba4444(bytes((0x0F, 0xF0)), 1, 1)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)
