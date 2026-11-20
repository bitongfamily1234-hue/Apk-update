#!/usr/bin/env python3
"""Extract and inspect Chaquopy app.imy Python bytecode (read-only).

The APK contains a ZIP archive named app.imy. This utility safely extracts
members, identifies the Python bytecode version, and writes a structural report
for compiled modules. It never modifies the APK or the source archive.
"""
from __future__ import annotations

import argparse
import dis
import importlib.util
import json
import marshal
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from types import CodeType
from typing import Any

KEYWORDS = re.compile(
    r"iff|texture|pixel|vctexture|format|decode|preview|export|resource|"
    r"bridge|replace|image|rgba|etc2|mipmap",
    re.IGNORECASE,
)


def safe_extract(archive: Path, output: Path) -> list[dict[str, Any]]:
    root = output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"unsafe path in app.imy: {info.filename}")
            target = (root / Path(*rel.parts)).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"archive path escapes output directory: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            target.write_bytes(data)
            record = {
                "name": info.filename,
                "compressed_bytes": info.compress_size,
                "uncompressed_bytes": info.file_size,
                "crc32": f"{info.CRC:08X}",
                "is_pyc": info.filename.endswith(".pyc"),
            }
            if record["is_pyc"]:
                record["pyc_magic_hex"] = data[:4].hex()
                record["pyc_header_matches_runtime"] = data[:4] == importlib.util.MAGIC_NUMBER
            records.append(record)
    return records


def code_tree(code: CodeType, module: str, path: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for const in code.co_consts:
        if isinstance(const, CodeType):
            name = const.co_name
            qualified = f"{path}.{name}" if path else name
            names = list(const.co_names)
            strings = [x for x in const.co_consts if isinstance(x, str)]
            combined = " ".join([module, qualified, *names, *strings])
            matched = bool(KEYWORDS.search(combined))
            instructions = list(dis.get_instructions(const))
            calls = [
                ins.argval for ins in instructions
                if ins.opname in {"LOAD_GLOBAL", "LOAD_METHOD", "LOAD_ATTR", "IMPORT_NAME", "IMPORT_FROM"}
            ]
            results.append({
                "module": module,
                "qualname": qualified,
                "firstlineno": const.co_firstlineno,
                "argcount": const.co_argcount,
                "posonlyargcount": const.co_posonlyargcount,
                "kwonlyargcount": const.co_kwonlyargcount,
                "locals": const.co_nlocals,
                "names": names,
                "strings": strings,
                "calls_and_attributes": calls,
                "bytecode_bytes": len(const.co_code),
                "matched_texture_terms": matched,
                "disassembly": dis.Bytecode(const).dis() if matched else "",
            })
            results.extend(code_tree(const, module, qualified))
    return results


def inspect_pyc(path: Path, relative: str) -> dict[str, Any]:
    data = path.read_bytes()
    result: dict[str, Any] = {
        "file": relative,
        "size": len(data),
        "magic_hex": data[:4].hex(),
        "runtime_magic_hex": importlib.util.MAGIC_NUMBER.hex(),
        "magic_matches_python_runtime": data[:4] == importlib.util.MAGIC_NUMBER,
    }
    if len(data) < 16:
        result["error"] = "pyc shorter than 16-byte header"
        return result
    if not result["magic_matches_python_runtime"]:
        result["error"] = "pyc magic does not match inspection interpreter; bytecode not unmarshalled"
        return result
    try:
        root = marshal.loads(data[16:])
    except Exception as exc:
        result["error"] = f"marshal failure: {type(exc).__name__}: {exc}"
        return result
    if not isinstance(root, CodeType):
        result["error"] = f"marshal root is not a code object: {type(root).__name__}"
        return result
    result["module_name"] = root.co_name
    result["firstlineno"] = root.co_firstlineno
    result["module_names"] = list(root.co_names)
    result["module_strings"] = [x for x in root.co_consts if isinstance(x, str)]
    result["functions"] = code_tree(root, relative)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive", default="assets/chaquopy/app.imy")
    ap.add_argument("--out", default="app-imy-extract")
    args = ap.parse_args()
    archive, output = Path(args.archive), Path(args.out)
    if not archive.is_file():
        raise SystemExit(f"app.imy not found: {archive}")
    members = safe_extract(archive, output)
    pyc_files = [m for m in members if m["is_pyc"]]
    reports: list[dict[str, Any]] = []
    for member in pyc_files:
        path = output / Path(*PurePosixPath(member["name"]).parts)
        reports.append(inspect_pyc(path, member["name"]))

    result = {
        "archive": str(archive),
        "archive_size": archive.stat().st_size,
        "zip_member_count": len(members),
        "pyc_count": len(pyc_files),
        "python_runtime": sys.version,
        "python_magic_hex": importlib.util.MAGIC_NUMBER.hex(),
        "members": members,
        "modules": reports,
    }
    (output / "app_imy_bytecode_report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# Chaquopy app.imy compiled module inventory",
        "",
        f"- Archive size: {result['archive_size']} bytes",
        f"- ZIP members: {result['zip_member_count']}",
        f"- Compiled Python modules: {result['pyc_count']}",
        f"- Inspection interpreter: {result['python_runtime']}",
        f"- Python bytecode magic: {result['python_magic_hex']}",
        "",
        "## Module summary",
        "",
        "| Module | Bytes | Functions/classes | Keyword-matched code objects | Bytecode unmarshalled |",
        "|---|---:|---:|---:|---|",
    ]
    for module in reports:
        functions = module.get("functions", [])
        matched = sum(1 for f in functions if f["matched_texture_terms"])
        lines.append(
            f"| {module['file']} | {module['size']} | {len(functions)} | {matched} | "
            f"{'yes' if 'error' not in module else 'no: ' + module['error']} |"
        )
    for module in reports:
        selected = [f for f in module.get("functions", []) if f["matched_texture_terms"]]
        if not selected:
            continue
        lines += ["", f"## {module['file']} — relevant function disassembly", ""]
        for fn in selected:
            lines += [
                f"### {fn['qualname']} (line {fn['firstlineno']}, "
                f"args {fn['argcount']}, {fn['bytecode_bytes']} bytecode bytes)",
                "",
                "Names: " + ", ".join(str(x) for x in fn["names"][:100]),
                "",
                "String constants: " + " | ".join(repr(x) for x in fn["strings"][:100]),
                "",
                "~~~text",
                fn["disassembly"].rstrip(),
                "~~~",
                "",
            ]
    report = "\n".join(lines) + "\n"
    (output / "app_imy_bytecode_report.md").write_text(report, encoding="utf-8")
    summary = {
        "archive": result["archive"],
        "archive_size": result["archive_size"],
        "zip_member_count": result["zip_member_count"],
        "pyc_count": result["pyc_count"],
        "python_runtime": result["python_runtime"],
        "python_magic_hex": result["python_magic_hex"],
        "unmarshalled_modules": sum("error" not in x for x in reports),
        "modules": [
            {
                "file": x["file"],
                "size": x["size"],
                "functions": len(x.get("functions", [])),
                "matched": sum(1 for f in x.get("functions", []) if f["matched_texture_terms"]),
                "error": x.get("error"),
            }
            for x in reports
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
