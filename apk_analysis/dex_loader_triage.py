#!/usr/bin/env python3
"""Extract DEX string references per method to locate the Chaquopy module loader.

This is a read-only triage tool. It does not decompile, patch, or modify DEX/APK
files. Output is a JSON and Markdown index of relevant strings and methods.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any

INTEREST = re.compile(
    r"decrypt|encrypt|cipher|crypto|secret|password|private.?key|"
    r"\.enc|encrypted|chaquo|python|module|loader|asset|aes|xor|"
    r"integrity|signature|sha[0-9]*|hmac|key",
    re.IGNORECASE,
)

# Standard Dalvik instruction widths in 16-bit code units.
WIDTH = [1] * 256
for op in (0x02,0x05,0x08,0x13,0x15,0x16,0x19,0x1A,0x1C,0x1F,0x22,0x23,
           0x29,0x2D,0x2E,0x2F,0x30,0x31):
    WIDTH[op] = 2
for op in (0x03,0x06,0x09,0x14,0x17,0x18,0x1B,0x24,0x25,0x26,0x2A,
           0x2B,0x2C):
    WIDTH[op] = 3
for op in range(0x32,0x3E):
    WIDTH[op] = 2
for op in range(0x44,0x6E):
    WIDTH[op] = 2
for op in range(0x6E,0x73):
    WIDTH[op] = 3
for op in range(0x74,0x79):
    WIDTH[op] = 3
for op in range(0x90,0xB0):
    WIDTH[op] = 2
for op in range(0xD0,0xE3):
    WIDTH[op] = 2
for op in (0xFA,0xFB):
    WIDTH[op] = 4
for op in (0xFC,0xFD,0xFE,0xFF):
    WIDTH[op] = 2


def uleb(data: bytes, offset: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if offset >= len(data) or shift > 35:
            raise ValueError("malformed ULEB128")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def parse_dex(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 0x70 or data[:4] != b"dex\n":
        raise ValueError(f"{path}: invalid DEX header")
    version = data[4:7].decode("ascii", errors="replace")
    file_size, header_size = struct.unpack_from("<II", data, 0x20)
    if header_size != 0x70 or file_size > len(data) or file_size < 0x70:
        raise ValueError(f"{path}: inconsistent DEX header size")
    data = data[:file_size]

    string_count, string_off = struct.unpack_from("<II", data, 0x38)
    type_count, type_off = struct.unpack_from("<II", data, 0x40)
    method_count, method_off = struct.unpack_from("<II", data, 0x58)
    class_count, class_off = struct.unpack_from("<II", data, 0x60)

    strings: list[str] = []
    for i in range(string_count):
        ptr = struct.unpack_from("<I", data, string_off + i * 4)[0]
        _utf16_length, pos = uleb(data, ptr)
        end = data.find(b"\0", pos)
        if end < 0:
            raise ValueError(f"{path}: unterminated DEX string #{i}")
        strings.append(data[pos:end].decode("utf-8", errors="replace"))

    types: list[str] = []
    for i in range(type_count):
        idx = struct.unpack_from("<I", data, type_off + i * 4)[0]
        types.append(strings[idx] if idx < len(strings) else f"<bad-string-{idx}>")

    method_ids: list[dict[str, Any]] = []
    for i in range(method_count):
        class_idx, proto_idx, name_idx = struct.unpack_from("<HHI", data, method_off + i * 8)
        method_ids.append({
            "class": types[class_idx] if class_idx < len(types) else f"<bad-type-{class_idx}>",
            "name": strings[name_idx] if name_idx < len(strings) else f"<bad-string-{name_idx}>",
            "proto_index": proto_idx,
        })

    methods_with_code: list[dict[str, Any]] = []
    class_method_count = 0
    for i in range(class_count):
        class_idx = struct.unpack_from("<I", data, class_off + i * 32)[0]
        class_data_off = struct.unpack_from("<I", data, class_off + i * 32 + 24)[0]
        class_name = types[class_idx] if class_idx < len(types) else f"<bad-type-{class_idx}>"
        if class_data_off == 0:
            continue
        pos = class_data_off
        static_n, pos = uleb(data, pos)
        instance_n, pos = uleb(data, pos)
        direct_n, pos = uleb(data, pos)
        virtual_n, pos = uleb(data, pos)
        for _ in range(static_n + instance_n):
            _diff, pos = uleb(data, pos)
            _flags, pos = uleb(data, pos)

        for method_n in (direct_n, virtual_n):
            method_idx = 0
            for _ in range(method_n):
                diff, pos = uleb(data, pos)
                access_flags, pos = uleb(data, pos)
                code_off, pos = uleb(data, pos)
                method_idx += diff
                class_method_count += 1
                if method_idx >= len(method_ids) or code_off == 0:
                    continue
                method = method_ids[method_idx]
                literals = extract_code_strings(data, code_off, strings)
                if literals:
                    methods_with_code.append({
                        "class": class_name,
                        "method": method["name"],
                        "method_index": method_idx,
                        "access_flags": access_flags,
                        "code_offset": code_off,
                        "strings": literals,
                    })

    interesting_strings = [
        {"index": i, "value": value}
        for i, value in enumerate(strings)
        if INTEREST.search(value)
    ]
    interesting_methods = []
    for item in methods_with_code:
        matched = [s for s in item["strings"] if INTEREST.search(s)]
        if matched or INTEREST.search(item["class"]) or INTEREST.search(item["method"]):
            item["matched_strings"] = matched
            interesting_methods.append(item)

    return {
        "file": path.name,
        "dex_version": version,
        "file_size": file_size,
        "string_count": string_count,
        "type_count": type_count,
        "method_id_count": method_count,
        "class_count": class_count,
        "class_encoded_method_count": class_method_count,
        "method_code_items_with_strings": len(methods_with_code),
        "interesting_string_count": len(interesting_strings),
        "interesting_method_count": len(interesting_methods),
        "interesting_strings": interesting_strings,
        "interesting_methods": interesting_methods,
    }


def extract_code_strings(data: bytes, code_off: int, strings: list[str]) -> list[str]:
    if code_off + 16 > len(data):
        return []
    insns_size = struct.unpack_from("<I", data, code_off + 12)[0]
    insns_start = code_off + 16
    insns_end = insns_start + insns_size * 2
    if insns_end > len(data):
        return []
    units = struct.unpack_from(f"<{insns_size}H", data, insns_start)
    found: list[str] = []
    i = 0
    while i < len(units):
        unit = units[i]
        opcode = unit & 0xFF
        if opcode == 0 and (unit >> 8) in (1, 2, 3):
            ident = unit >> 8
            if ident == 1 and i + 1 < len(units):
                width = 4 + units[i + 1] * 2
            elif ident == 2 and i + 1 < len(units):
                width = 2 + units[i + 1] * 4
            elif ident == 3 and i + 3 < len(units):
                element_width = units[i + 1]
                size = units[i + 2] | (units[i + 3] << 16)
                width = 4 + (element_width * size + 1) // 2
            else:
                width = 1
        elif opcode == 0x1A and i + 1 < len(units):
            string_idx = units[i + 1]
            if string_idx < len(strings):
                found.append(strings[string_idx])
            width = 2
        elif opcode == 0x1B and i + 2 < len(units):
            string_idx = units[i + 1] | (units[i + 2] << 16)
            if string_idx < len(strings):
                found.append(strings[string_idx])
            width = 3
        else:
            width = WIDTH[opcode]
        i += max(1, width)
    return found


def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# DEX encrypted-module loader triage",
        "",
        "Read-only string-reference scan of DEX files in the APK repository.",
        "This is triage, not a full decompilation or proof of a crypto scheme.",
        "",
        "## DEX summary",
        "",
        "| File | Version | Classes | Method IDs | Methods with strings | Interesting strings | Interesting methods |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['file']} | {r['dex_version']} | {r['class_count']} | "
            f"{r['method_id_count']} | {r['method_code_items_with_strings']} | "
            f"{r['interesting_string_count']} | {r['interesting_method_count']} |"
        )
    for r in results:
        lines += ["", f"## {r['file']} — candidate strings", ""]
        for item in r["interesting_strings"][:300]:
            value = item["value"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"- string #{item['index']}: {value}")
        lines += ["", f"## {r['file']} — methods referencing candidate strings", ""]
        for item in r["interesting_methods"][:250]:
            values = "; ".join(repr(x) for x in item["matched_strings"][:30])
            lines.append(
                f"- {item['class']}.{item['method']} "
                f"(method id {item['method_index']}, code offset {item['code_offset']}): {values}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".", help="repository root")
    ap.add_argument("--out", default="dex-loader-triage", help="output directory")
    args = ap.parse_args()
    root = Path(args.repo)
    out = root / args.out
    out.mkdir(parents=True, exist_ok=True)
    inputs = sorted(root.glob("classes*.dex"))
    if not inputs:
        raise SystemExit(f"No classes*.dex files in {root}")
    results = [parse_dex(path) for path in inputs]
    (out / "dex_loader_triage.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    report = render_markdown(results)
    (out / "dex_loader_triage.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
