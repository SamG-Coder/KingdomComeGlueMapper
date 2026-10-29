# Clothing & Body Test — developer tool

This standalone tool inspects the **actual converted assets** in a built or installed mod. It is deliberately separate from consumer Setup and is not bundled into the setup executable. It never installs, upgrades, or edits a game package.

## Open the 3D workbench

From the repository root, using the development environment:

```powershell
.venv/Scripts/python.exe tools/clothing_lab_app.py
```

Steam game locations are detected as starting values. Select a mod folder containing `Data/*.pak`, the KCD2 installation supplying the native rules, and the KCD1 installation for source-soul audits. All three may also be supplied with `--package`, `--target`, and `--source`.

The optional **Developer candidate** field, or `--overlay` on either the viewer or audit CLI, reads a separate repair overlay over that package. The game files are unchanged. This lets the viewer reproduce the same candidate used by the batch checklist.

The viewer supports:

- Male and female character presets, every registered clothing component, and custom layer stacks.
- Individual part visibility, textured/layer-colour/wireframe views, orbit, pan, zoom, front/back/side views, and separated layers.
- Native highest-layer selection of one clothing mesh per equipment region, retained body layers, and per-region vertex hiding. The masks-disabled comparison keeps the same native mesh selection.
- Material two-sided flags and agreement between render/internal skinning colour buffers, including native hiding bits.
- Available mesh LODs; each part retains its last authored LOD when the requested level is farther away. The report records the effective level. Missing base meshes and broken LOD chains are errors.
- A weighted arm-bend stress pose and saved PNG views accompanied by JSON selections, camera state, package signature, and findings.
- Audit results that open their exact component combination in the viewer.

The diffuse preview decodes streamed DDS mip families, including half-float clothing macro textures and their transparency. No game content is distributed with this tool. The locally bundled Three.js renderer is MIT licensed; see `ui/vendor/README.md`.

## Automated checks

Run every converted outfit through the structural and geometry checks:

```powershell
.venv/Scripts/python.exe tools/clothing_lab_test.py `
  --package "D:/path/to/built-or-installed-mod" `
  --target "D:/path/to/KingdomComeDeliverance2" `
  --mode presets --geometry --reports reports/clothing-lab
```

Enumerate **every KCD1 soul record**, resolve body/head/hair/beard references, both `clothing_id` and `clothing2_id` garment slots, and `armor2clothing_attachment` details, then validate converted outfits where present:

```powershell
.venv/Scripts/python.exe tools/clothing_lab_test.py `
  --package "D:/path/to/built-or-installed-mod" `
  --target "D:/path/to/KingdomComeDeliverance2" `
  --source "D:/path/to/KingdomComeDeliverance" `
  --mode souls --geometry --strict --reports reports/clothing-lab
```

Source mode always enumerates every shared database soul. Missing conversions are reported as `not_converted`, not passed or omitted. Broken source references produce `source_error`. Empty source bodies may be supplied by a world instance; the audit retains that distinction and does not invent a default body. Source clothing attachments are retained in each report. World-instance overrides, equipment changes driven by quests, and probabilistic inventory selection require a further native test.

The combination modes are:

| Mode | Coverage |
| --- | --- |
| `presets` | All converted character presets, optionally filtered by `--gender Female` or `Male`. |
| `souls` | All shared KCD1 soul rows, with explicit converted/unconverted coverage. |
| `singles` | Every registered garment of the selected body's gender. |
| `pairs` | Every unordered garment pair of that gender, including invalid pairs so slot conflicts are visible. |
| `layers` | Cartesian product of alternatives per declared part/layer signature, including an empty choice for each slot. Cross-slot conflicts are still validated. |

Combination modes require `--body <preset-id>` (shown in report JSON). Enumeration is deterministic. `--limit 250 --offset 0` bounds a batch, and the report supplies `next_offset` and the full `total`. The UI advances its cursor after a batch. A partial batch is never presented as exhaustive coverage. Large Cartesian products can require many batches; use singles and pairs first. The mixed-outfit mask warning is intentional: unrelated components can reuse hiding bit numbers with different meanings.

Reports go to `reports/clothing-lab` by default. Structural failures use `error`; uncertain geometric findings use `review`; `checked` means only the enabled checks found no issue. `enumeration_complete` describes whether all planned cases were visited. `complete` additionally requires every case checked without findings, no catalog errors, and no cancellation. `runtime_verified` always remains false.

The command exits **1** for structural/asset/source errors, **2** if the audit could not run, and **0** otherwise. Add `--strict` to also fail on review findings, missing conversions, or incomplete coverage. This makes it usable from CI without claiming a normal exit code proves native rendering correctness.

## What is simulated

Native role, gender, inherited archetype, equipment-part, hiding groups and body-layer definitions are read from the selected KCD2 `Data/Tables.pak`, with each table hash recorded. Layer comparisons use `body_layer.xml`'s `layer_order`, not the layer IDs: decoration layer 4 is above layers 5–7, while underwear layer 8 is near the body. Checks cover unresolved layers, same-slot conflicts, undefined hiding groups, cross-outfit masks, missing meshes/materials/textures, mesh indices and weights, and source references. Differences from native role templates are review findings because a converted layout can intentionally differ.

The geometry pass tests bounded surface-normal segments against actual garment triangles, filtering ray hits by material-subset UV alpha. A conservative spatial index reduces work without changing the segment result. It reports hidden skin without an opaque cover, clothing beneath visible skin, and higher layers intersecting lower ones. `Nodraw` material slots do not count as opaque coverage. These are **suspected** problems: seams, intended contact, wrinkles, and the bounded normal-ray policy can produce review findings.

This is not a reimplementation of KCD2's closed-source character manager. In particular, equip-slot arbitration, native cloth physics, cloth collision proxies, engine animation, material-atlas microdetail, iris/skin shading, and distant streaming selection are not reproduced. The synthetic stress pose is a viewer aid; automated geometry checks use the exported bind pose. Rendering a selectable combination does not prove the native inventory system can equip it. Shared armor-archetype findings call out that boundary explicitly.

## Whole wardrobe audit and isolated repair candidates

The developer matrix command enumerates every registered garment and every
same-gender garment pair, then checks each existing character preset. It writes
`checklist.csv`, `cases.jsonl`, `confirmed.json`, and `summary.json`. Rows retain
the exact component selection, representative body, native-table result,
geometry result, and an explicitly unverified runtime field.

```powershell
.venv/Scripts/python.exe tools/clothing_lab_matrix.py `
  --package "D:/path/to/built-or-installed-mod" `
  --target "D:/path/to/KingdomComeDeliverance2" `
  --build-layer-candidate reports/clothing-lab/my-layer-candidate `
  --output reports/clothing-lab/my-matrix
```

`--build-layer-candidate` creates a separate overlay for missing native layer
inheritance and unsupported upper-body partitions. It reads both `SkinElement`
and `VClothElement` layer definitions. Merging unsupported partitions preserves
the triangle count and material bindings. Unsupported native roles remain
listed in `repair.json`; they are not assigned invented layer numbers. The
original package is never changed. Pass an existing candidate with `--overlay`.
This candidate does not rebuild skin-hiding masks or repair equipment arbitration.

The matrix computes **exact symbolic counts** over full layer selections using
unary registration checks, declared part/layer occupancy, repeated archetypes,
and exclusive native body-subpart/layer reservations. Different archetypes can
reserve the same native inventory slot even when their mesh regions differ.
Validation contract version 5 includes these reservations, native draw-region
selection, culled garment coverage and hair/headwear intersection diagnostics.
The new geometry checks flag review candidates in the bind pose; they do not
certify animation or every pixel of an alpha-cut surface.
Lower-layer meshes discarded by the native attachment manager need
review; they cannot be counted as confirmed coverage. Old resume/checklist
evidence is rejected and must be rerun.
These counts are not rendered tests. Other engine equipment exclusions,
body-dependent masks, cloth simulation, and full-outfit geometry are outside
that proof. In particular, passing every pair cannot establish that a complete
outfit works.

For a tractable full space, `--full-layers Female` or `--full-layers Male`
enumerates every layer selection and runs bind-pose geometry checks on those
passing the native table checks. Results stream to `cases.jsonl.gz`. The command
refuses spaces above one million cases instead of silently sampling them.
Body, package signature, rule-table hash, all visited cases and geometry-run
counts are recorded. A completed audit can contain failures; command completion
is not an all-clear result.

Long pair runs can be split with `--pair-range START STOP` (zero-based,
exclusive stop). Such reports are marked incomplete. Resume completed rows
with `--resume-from reports/clothing-lab/previous-run`; specify this option
multiple times to combine non-conflicting batches. Output goes to a fresh
directory. Package signatures, rule-table evidence, validator contract version,
and exact selections must match. A truncated final JSONL write can be discarded; corrupt completed rows
are rejected. An unbounded final run with all batch reports fills gaps and
produces a complete checklist.

`clothing_lab.checklist.write_checklist(matrix, full_layers, repairs, output)`
generates a Markdown checklist and a CSV of exact selections passing both table
and geometry checks. It rejects incomplete matrix/full-layer reports and
mismatched candidate signatures. Unclothed baselines are counted separately.
Optional `source_report` and `captures` arguments include a completed soul audit
and developer-renderer examples, provided their candidate and rule hashes match.

## Native appearance playtests

`upgrade_source_appearances.py` rebuilds explicitly selected, already imported
source people through the same general appearance converter. It reads their
identity and namespace from `PersonImport` receipts. It does not contain actor
or garment exceptions and is a developer command, separate from Setup UI.

```powershell
python tools/upgrade_source_appearances.py `
  --source "D:/Games/KingdomComeDeliverance" `
  --target "D:/Games/KingdomComeDeliverance2" `
  --installed "D:/Games/KingdomComeDeliverance2/Mods/kingdomcomegluemapper" `
  --output "reports/clothing-lab/native-candidate" `
  --person ska_blanka --person ska_guard2
```

This verifies the installed receipt, retains a recovery package at `baseline/`,
and produces a complete candidate at `package/`. The native actor packer builds
streamed textures and bounded PAK shards; the workbench overlay itself must not
be copied into the game. Changed files use replacement writes so hard-linked
checkpoints remain intact. Unrelated quests, schedules, inventory and equipment
bindings stay byte-identical. Unknown ownership or changed appearance identities
stop the migration. The ordinary `desktop_setup.install_transaction` then
verifies and installs the candidate. The report records selected people and
changed files, and leaves `runtime_verified` false until a separate native test.

Use a fresh new game to exercise initial outfit selection. Keep native screenshots
and engine observations separate from WebGL captures and symbolic counts. A
four-person playtest does not certify every source soul or arbitrary combination.

For a whole-cast developer audit, use `--all-imported --overlay-only` instead of
`--person`. This resolves every existing `PersonImport` receipt, including the
older named namespaces, and rebuilds one person at a time. It records source
code hashes and the base-package signature, writes changed runtime assets only,
and discards temporary conversion intermediates after each successful person.
It retains a progress report and completed files if a later conversion fails.
A failed batch is explicitly incomplete; it is not a successful audit.

The overlay includes a single migration of shared appearance tables, removing
only exact definitions replaced by generated canonical tables. A shared name
prefix does not grant ownership of gameplay inventories: equipped wrappers,
carried items and shop stock remain intact. New per-person definitions retain
their original identities. Both rebuild modes validate the NPC equipment chain
before declaring their output complete.
This prevents legacy shared tables from selecting stale clothing or registering
conflicting hiding groups. Open the result through `--overlay` in the workbench
or audit CLI. It is **not an installable package** and must not be copied into
the game as a standalone mod.

Source armour-linked straps, belts, collars, sleeves and apron knots are joined
to their owning garment before native region composition. The importer uses
the source relationship tables, gender and tint; it preserves source triangles,
materials and LODs. Exact duplicate material slots are coalesced to avoid
repeated texture loads. Garment hiding must be safe for every triangle touching
a masked vertex. Head, hair and beard also receive conditional coverage from
opaque headwear. These rules apply to source types, without named-NPC exceptions.

Validation contract 6 follows each imported person's named Storm equipment rule
through nested `InventoryPresetRef`, `ClothingPresetRef`, item GUIDs and clothing
components. Missing or conflicting definitions, cycles and empty outfits are
errors even when the mesh family exists. Checked actor presets use the reachable
clothing components. These checks do not emulate Storm precedence, later quest
inventory changes or rendered animation.

Persistent body geometry keeps its source shape. Fixed-outfit fitting may shrink
inner garments, but must not bake an outfit-dependent shrink/chest morph into
skin that remains after unequipping the outfit. Conditional equipment masks
control covered skin instead.

To repair bodies in an existing developer package without rebaking textures:

```powershell
.venv/Scripts/python.exe tools/upgrade_source_bodies.py --source <KCD1> --target <KCD2> --installed <mod> --output <new-overlay>
```

This standalone developer tool supports `--person <source-name>` or all imported
people by default. It verifies unchanged topology, UVs and skinning identities,
keeps installed material bindings and recomputes body coverage. Its overlay is
for inspection and must be promoted to a verified full package before install.
It is not part of consumer Setup.

## Regression tests

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_clothing_lab*.py" -v
```

Tests use synthetic, redistributable geometry for inheritance, source completeness, DDS reconstruction, transparent sleeves, hidden skin, penetration, native body replacement, Nodraw, collision-safe asset lookup, deterministic combinations, cancellation, and spatial-index equivalence. Optional Playwright/Edge tests exercise real WebGL output, masking, stress poses, capture, filters, and report navigation. No local game paths are required for these tests.
