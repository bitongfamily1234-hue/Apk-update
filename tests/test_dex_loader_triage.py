from apk_analysis.dex_loader_triage import extract_code_strings, uleb


def test_uleb_single_byte():
    assert uleb(bytes((0x7F,)), 0) == (127, 1)


def test_uleb_multibyte():
    assert uleb(bytes((0xAC, 0x02)), 0) == (300, 2)


def test_extract_const_string_reference():
    # const-string v0, string@1 ; return-void
    units = (0x001A, 0x0001, 0x000E)
    code = bytearray(16 + 2 * len(units))
    code[12:16] = len(units).to_bytes(4, "little")
    for i, unit in enumerate(units):
        code[16 + 2*i:18 + 2*i] = unit.to_bytes(2, "little")
    assert extract_code_strings(bytes(code), 0, ["ignored", "decryptData"]) == ["decryptData"]


def test_extract_const_string_jumbo_reference():
    # const-string/jumbo v0, string@1 ; return-void
    units = (0x001B, 0x0001, 0x0000, 0x000E)
    code = bytearray(16 + 2 * len(units))
    code[12:16] = len(units).to_bytes(4, "little")
    for i, unit in enumerate(units):
        code[16 + 2*i:18 + 2*i] = unit.to_bytes(2, "little")
    assert extract_code_strings(bytes(code), 0, ["ignored", "assets/encrypted_modules"]) == [
        "assets/encrypted_modules"
    ]


def test_const_wide_width_preserves_following_string():
    # const-wide uses five code units before the const-string.
    units = (0x0018, 0x0000, 0x0000, 0x0000, 0x0000, 0x001A, 0x0001, 0x000E)
    code = bytearray(16 + 2 * len(units))
    code[12:16] = len(units).to_bytes(4, "little")
    for i, unit in enumerate(units):
        code[16 + 2*i:18 + 2*i] = unit.to_bytes(2, "little")
    assert extract_code_strings(bytes(code), 0, ["ignored", "crypto key"]) == ["crypto key"]


def test_instruction_widths_preserve_following_string():
    # instance-of (2 code units), followed by const-string and return-void.
    units = (0x0020, 0x0000, 0x001A, 0x0001, 0x000E)
    code = bytearray(16 + 2 * len(units))
    code[12:16] = len(units).to_bytes(4, "little")
    for i, unit in enumerate(units):
        code[16 + 2*i:18 + 2*i] = unit.to_bytes(2, "little")
    assert extract_code_strings(bytes(code), 0, ["ignored", "module loader"]) == ["module loader"]
