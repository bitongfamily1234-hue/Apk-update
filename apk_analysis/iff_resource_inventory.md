# IFF texture and resource inventory

## Scope and method

The scanner apk_analysis/iff_texture_inventory.py inventories the decompressed object and pixel allocations in the supplied IFFs. It searches 16-byte-aligned object offsets and accepts only candidate VCTEXTURE structures with plausible format IDs, dimensions, mip counts and pointer values. It selects the type-2 paired file entry that owns each descriptor, applies the native pointer bias (pointer_word - 1), then verifies the entire mip-0 range lies inside the pixel allocation.

**These totals are structurally valid candidates, not proof that every match is a standalone live texture.** The scan defaults to 16–4096 width/height and 16-byte descriptor alignment. Candidate counts may change when scan bounds or alignment are changed.

## Candidate totals

| IFF | File entries | Type-1 entries | Paired type-2 entries | Texture candidates |
|---|---:|---:|---:|---:|
| GAMEDATA.IFF | 202 | 53 | 149 | 163 |
| GOOEYGLOBAL(1).IFF | 68 | 23 | 45 | 35 |
| GOOEYFRONTEND(1).IFF | 380 | 53 | 327 | 242 |
| **Total** | **650** | **129** | **521** | **440** |

Format-ID distribution:

| IFF | 1 | 2 | 4 | 5 | 6 | 7 | 15 | 16 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GAMEDATA | 9 | 8 | 6 | 1 | 9 | 4 | 23 | 103 |
| GOOEYGLOBAL | 0 | 3 | 7 | 0 | 1 | 0 | 4 | 20 |
| GOOEYFRONTEND | 0 | 29 | 26 | 0 | 5 | 4 | 43 | 135 |
| **Total** | **9** | **40** | **39** | **1** | **15** | **8** | **70** | **258** |

All accepted candidates in these three files have mip count 1 and depth 1 in the current scan. This does not establish that the IFFs contain no mipmapped or array textures outside the current scan's descriptor-alignment and field assumptions.

## Recovered and recognizable texture families

- **GAMEDATA:** RGB565 stadium lighting / arena views; RGBA4444 sponsor marks and legacy NBA logos; additional large packed-image atlases and grayscale/single-channel data.
- **GOOEYGLOBAL:** the 256×128 “2K NAV” graphic, menu frame components, button/glow assets, and shared visual atlases.
- **GOOEYFRONTEND:** a large collection of NBA 2K20 branding, score/menu controls, button states, decorative elements, arena backgrounds, photo/award imagery, and packed UI atlases.

The known PNG-preview decoders in the inventory utility cover format 2 (RGB565), format 4 (RGBA4444), format 5 (A8 visualization), and format 6 (R8 visualization). Exact source bytes can be dumped for every candidate with the --export-raw option. Formats without a confirmed PNG channel decoder are not converted into invented images.

Format 15 is ETC2 RGB8, confirmed by two independent exact reference-image matches already documented in texture_pointer_recovery.md. Format 16 has a confirmed 16-byte-per-4×4 block size but its current EAC/RGBA reference lead is not an exact match. Formats 17 and 18 have native size rules but no confirmed examples in these three scans.

## Other resource types: scene / model lead (side investigation only)

The file-entry tables also contain **129 type-1 entries** across these files. Their records point into the object allocation and include opaque headers and embedded UTF-16 text in some cases (for example, GAMEDATA contains lines_ts.bin and lines_cs.bin). These entries are not the paired object/pixel records used for VCTEXTURE.

The type-1 entries may include non-texture resources, but no scene, VCMODEL, mesh, or OBJ serialization format has been identified from this pass. The IDs and header markers are retained in the inventory JSON as leads. No model/scene extractor has been written yet because assigning those structures without a verified layout risks generating misleading geometry or corrupt offsets. This remains a side track; texture extraction and eventual APK integration stay the priority.

## Running the inventory tool

From the repository root:

```bash
python apk_analysis/iff_texture_inventory.py GAMEDATA.IFF GOOEYGLOBAL.IFF GOOEYFRONTEND.IFF --out iff_texture_inventory
```

This writes one JSON inventory per IFF and contact sheets for formats 2, 4, 5 and 6. To also save the exact source bytes for all candidates:

```bash
python apk_analysis/iff_texture_inventory.py GAMEDATA.IFF GOOEYGLOBAL.IFF GOOEYFRONTEND.IFF --out iff_texture_inventory --export-raw
```

## Main-project next steps

1. Run the inventory on additional game IFFs and match records to the reference image set.
2. Integrate the proven paired-entry pointer resolution into the tool's actual IFF texture-read path.
3. Reuse the existing ETC2 decoder path (the packaged Python assets include etc2.py.enc) for format 15; validate the decoder integration with the exact reference pairs.
4. Keep format 16 raw until its reference mismatch is explained; investigate formats 1, 3 and 7 for channel conversion after the main formats are integrated.
5. Reconstruct multi-mip and subresource alignment from the native offset functions before removing the one-mip restriction.

The encrypted application modules remain byte-for-byte archived; this inventory script is a separate diagnostic utility and does not patch the APK.
