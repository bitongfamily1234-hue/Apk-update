# APK and IFF analysis

Store extracted APK components and loader/decryption investigation notes here. Avoid committing large APK binaries or private signing material.

## Texture investigation tools

- [iff_texture_resolver.py](iff_texture_resolver.py): diagnostic resolver/exporter for a known VCTEXTURE descriptor offset.
- [iff_texture_inventory.py](iff_texture_inventory.py): 16-byte-aligned candidate scanner, JSON inventory, PNG previews for confirmed linear formats, contact sheets, and optional raw payload dumps.
- [native_texture_format_survey.md](native_texture_format_survey.md): ARM64 native format sizing/conversion findings, including confidence boundaries.
- [iff_resource_inventory.md](iff_resource_inventory.md): current scan totals and non-texture resource leads.
- [texture_pointer_recovery.md](texture_pointer_recovery.md): paired file-entry pointer validation and exact reference matches.

The inventory script's accepted records are candidates filtered by structure and bounds, not guaranteed-live textures. Use the descriptor/hash/entry metadata and reference images to validate each candidate. The resolver and inventory utilities do not modify the source IFF or APK.

## Integration status

The repository contains the APK's encrypted Python modules under assets/py/encrypted_modules. The corresponding plaintext application source is not currently available in this repository, so the diagnostic resolver is not yet wired into the packaged TKM Modding Tool. Keep experimental code separate from the encrypted originals until the actual runtime loading/patch path has been verified.
