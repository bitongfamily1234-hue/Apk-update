#!/usr/bin/env python3
"""Selectively decompile the Chaquopy modules relevant to IFF texture export.

The decompiler is built separately by the dedicated GitHub Actions workflow.
This script only verifies expected .pyc files and calls the supplied pycdc
executable; original app.imy and .py.enc assets remain unchanged.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

TARGETS = (
    "tkm_bridge.pyc",
    "iffengine/__init__.pyc",
    "iffengine/core.pyc",
    "iffengine/textureformats.pyc",
    "iffengine/etc2.pyc",
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pyc-root", default="app-imy-extract")
    ap.add_argument("--pycdc", default="pycdc")
    ap.add_argument("--out", default="app-imy-decompiled")
    args = ap.parse_args()

    pyc_root = Path(args.pyc_root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pycdc = Path(args.pycdc)
    if not pycdc.is_file():
        raise SystemExit(f"pycdc executable not found: {pycdc}")

    results = []
    for rel in TARGETS:
        source = pyc_root / rel
        if not source.is_file():
            results.append({"file": rel, "status": "missing"})
            continue
        destination = out / (rel.replace("/", "__").removesuffix(".pyc") + ".py")
        run = subprocess.run(
            [str(pycdc.resolve()), str(source.resolve())],
            capture_output=True,
            text=True,
            check=False,
        )
        destination.write_text(run.stdout, encoding="utf-8")
        stderr_path = destination.with_suffix(".stderr.txt")
        stderr_path.write_text(run.stderr, encoding="utf-8")
        results.append({
            "file": rel,
            "output": destination.name,
            "status": "ok" if run.returncode == 0 and run.stdout.strip() else "partial_or_error",
            "returncode": run.returncode,
            "stdout_bytes": len(run.stdout.encode("utf-8")),
            "stderr": run.stderr[:4000],
        })

    (out / "decompile_status.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
