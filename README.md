# Apk-update

Repository for the TKM Modding Tool 2.5 investigation and maintenance.

## Repository layout
- `encrypted_modules/`: original encrypted Python modules, preserved byte-for-byte.
- `docs/`: investigation notes, reverse-engineering findings, and project documentation.
- `apk_analysis/`: APK extraction and loader/decryption analysis notes.
- `texture_repair/`: texture-format investigation, decoder work, and repair notes.
- `tests/`: test plans and regression cases.

## Status
The encrypted module files are archived unchanged. Decryption/loading details and texture decoder behavior are still under investigation; no plaintext source or decryption key has been recovered yet.

## Safety
Keep original encrypted files untouched. Put recovered/decrypted material, experiments, and patches in separate folders/files. Record checksums and test changes against copies.
