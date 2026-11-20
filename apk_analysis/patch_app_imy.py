#!/usr/bin/env python3
"""Build a patched Chaquopy app.imy without modifying the APK or .py.enc assets.

The patch wraps the original iffengine.core module code object, then installs
the tested mobile IFF correction after the original engine initializes. The
original compiled core code object is embedded unchanged as a marshal constant.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import marshal
import struct
import sys
import time
import zipfile
from pathlib import Path

SENTINEL = "TKM_ORIGINAL_CORE_CODE_OBJECT_91C3F7"
CORE_MEMBER = "iffengine/core.pyc"
PATCH_MEMBER = "iffengine/mobile_iff_patch.pyc"

WRAPPER_SOURCE = f'''__tkm_original_core_code = "{SENTINEL}"
exec(__tkm_original_core_code, globals())
from . import mobile_iff_patch as _tkm_mobile_iff_patch
_tkm_mobile_iff_patch.install(globals())
del _tkm_mobile_iff_patch
del __tkm_original_core_code
'''


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def wrap_core(core_pyc: bytes) -> bytes:
    if len(core_pyc) < 16 or core_pyc[:4] != importlib.util.MAGIC_NUMBER:
        raise ValueError("core.pyc is not compiled for this Python interpreter")
    original_code = marshal.loads(core_pyc[16:])
    wrapper = compile(WRAPPER_SOURCE, getattr(original_code, "co_filename", "iffengine/core.py"), "exec")
    constants = list(wrapper.co_consts)
    matches = [i for i, value in enumerate(constants) if value == SENTINEL]
    if len(matches) != 1:
        raise ValueError("could not identify the unique original-code sentinel")
    constants[matches[0]] = original_code
    wrapped_code = wrapper.replace(co_consts=tuple(constants))
    return core_pyc[:16] + marshal.dumps(wrapped_code)


def build(original_imy: Path, patch_source: Path, build_json: Path,
          output_imy: Path, output_manifest: Path) -> dict:
    original_bytes = original_imy.read_bytes()
    manifest = json.loads(build_json.read_text(encoding="utf-8"))
    expected = manifest.get("assets", {}).get("app.imy")
    actual = sha1(original_bytes)
    if expected and expected.lower() != actual:
        raise ValueError(
            f"Chaquopy build.json app.imy hash mismatch: manifest={expected}, actual={actual}; "
            "refusing to patch an unverified archive"
        )

    with zipfile.ZipFile(original_imy, "r") as source_zip:
        names = source_zip.namelist()
        if CORE_MEMBER not in names:
            raise ValueError(f"missing {CORE_MEMBER} in app.imy")
        core_pyc = source_zip.read(CORE_MEMBER)
        patched_core = wrap_core(core_pyc)
        patch_code = compile(patch_source.read_text(encoding="utf-8"), PATCH_MEMBER[:-1], "exec")
        patch_pyc = core_pyc[:16] + marshal.dumps(patch_code)
        replaced = {CORE_MEMBER: patched_core, PATCH_MEMBER: patch_pyc}
        output_imy.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_imy, "w") as target_zip:
            target_zip.comment = source_zip.comment
            for info in source_zip.infolist():
                data = replaced.pop(info.filename, None)
                if data is None:
                    data = source_zip.read(info.filename)
                target_zip.writestr(info, data)
            for name, data in replaced.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.date_time = time.localtime()[:6]
                target_zip.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)

    output_bytes = output_imy.read_bytes()
    # Verify exact preservation of all original members except the replaced core.
    with zipfile.ZipFile(original_imy, "r") as old_zip, zipfile.ZipFile(output_imy, "r") as new_zip:
        old_names = set(old_zip.namelist())
        new_names = set(new_zip.namelist())
        if new_names != old_names | {PATCH_MEMBER}:
            raise ValueError("patched app.imy member set differs from the expected original-plus-patch set")
        for name in old_names - {CORE_MEMBER}:
            if old_zip.read(name) != new_zip.read(name):
                raise ValueError(f"original app.imy member unexpectedly changed: {name}")
        if new_zip.read(PATCH_MEMBER) != patch_pyc:
            raise ValueError("new mobile patch bytecode failed round-trip verification")
        if new_zip.read(CORE_MEMBER) != patched_core:
            raise ValueError("wrapped core bytecode failed round-trip verification")

    manifest.setdefault("assets", {})["app.imy"] = sha1(output_bytes)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8")
    return {
        "original_app_imy_bytes": len(original_bytes),
        "patched_app_imy_bytes": len(output_bytes),
        "original_app_imy_sha1": actual,
        "patched_app_imy_sha1": sha1(output_bytes),
        "manifest_hash_updated": manifest["assets"]["app.imy"],
        "original_member_count": len(names),
        "patched_member_count": len(names) + 1,
        "core_pyc_original_bytes": len(core_pyc),
        "core_pyc_patched_bytes": len(patched_core),
        "patch_module_pyc_bytes": len(patch_pyc),
        "changed_original_members": [CORE_MEMBER],
        "added_members": [PATCH_MEMBER],
        "all_other_original_members_verified_unchanged": True,
        "apk_signature_or_integrity_constants_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-imy", default="assets/chaquopy/app.imy")
    parser.add_argument("--patch-source", default="apk_analysis/mobile_iff_patch.py")
    parser.add_argument("--build-json", default="assets/chaquopy/build.json")
    parser.add_argument("--output-imy", default="patched-app/app.imy")
    parser.add_argument("--output-manifest", default="patched-app/build.json")
    args = parser.parse_args()
    result = build(
        Path(args.app_imy), Path(args.patch_source), Path(args.build_json),
        Path(args.output_imy), Path(args.output_manifest),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
