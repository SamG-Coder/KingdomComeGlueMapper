# Native clothing selection and workbench parity

## Confirmed cause

On 25 October 2026, the public KCD2 Modding Tools runtime showed Bianca's
underdress in the torso slot cache, but `AttachmentsByName/clothing_torso`
contained only her outer dress. Both garments' hiding groups were active.
The lower garment hid body skin even though its sleeves were not drawn.
The developer workbench incorrectly rendered both garment meshes and therefore
concealed this defect. Distinct inventory archetypes alone did not solve it.

The green-dress stall trader is `ska_merchant4`. The previous `ska_woman27`
fixture was a different person and did not validate that trader.

## General conversion correction

After source fitting, native material conversion and coverage generation,
`clothing_native_composition.py` groups source garments by equipment region.
It uses KCD2's `body_layer.xml` order to select the regional owner, canonicalizes
the source skeleton palettes, and composes all required surfaces into its mesh.
Every source triangle and material subset is retained at each authored LOD;
shorter LOD chains retain their last level. Coverage bits and all constituent
hiding-group declarations are retained.

The overlapping source layers become one regional outfit item. Lower items
are removed from that preset and item table instead of claiming they can be
independently removed while remaining baked into the outer mesh. Shoes, headwear
and other non-overlapping regions remain separate. The source underwear
fallback keeps its existing appearance identity. This is source-outfit
composition, not arbitrary runtime recombination of independently equipped items.
There are no NPC-name, dress-name, coordinate or per-item scale exceptions.

Native material features also require identical vertex RGBA in the render
stream and the internal skinning buffer. Conversion now writes both through
the render-to-internal remap and rejects conflicting seam values. The previous
candidate had 21 affected mesh files. The new four-person candidate has 47
meshes and no such discrepancies.

Composed multi-materials deduplicate identical feature-slot declarations.
KCD2 scopes these declarations across the whole multi-material; repeating
them in each source submaterial produced native errors. Conflicting slot
meanings fail conversion and are also rejected by the workbench.

Coverage uses the actual converted surface and the finest texture alpha cut.
The trader's two dress textures have 1x1 alpha averages of approximately 0.47
and 0.72, below their 0.77 and 0.75 cutout thresholds. Requiring every mip to
pass incorrectly rejected every covering triangle and left skin visible
through the bodice. Bilinear footprints at the authored cut and conservative
triangle boundaries still preserve necklines and cutouts. Legacy KCD1
triangle-culling caches no longer override the converted surface. Texture
filtering and distant appearance still need native checks.

## Developer workbench correction

- Select the highest native layer per region; do not draw every selected item.
- Retain hiding groups from equipped but unselected lower layers.
- Apply the selected owner's `KeepBodyLayer`, not a discarded lower owner's.
- Honor native material two-sided flags instead of always drawing backfaces.
- Report render/internal colour-buffer disagreements as errors.
- Exclude discarded region layers from confirmed combination counts.
- Invalidate earlier matrix/checklist evidence using validation contract 4.

The masks-disabled view changes hiding bits only; it does not bring discarded
native attachments back into view. Native physics, animations and shader
appearance still require an engine test.

## Current bounded package

`reports/clothing-lab/native-regions-v8-20261025/` contains the private rebuilt
package, recovery baseline, source conversion receipts, and native screenshots.
It rebuilds Bianca, the affected stall trader, Father and `ska_guard2` from
their source soul records. Installation uses the verified transaction and
does not modify unrelated world/schedule resources.

- [x] Regression reproduces a discarded inner mesh with an active skin mask.
- [x] Region composition retains source triangles, materials and LODs.
- [x] Native material colour buffers agree for all 47 rebuilt meshes.
- [x] Installed through the package verification transaction.
- [x] Native v7 capture: Bianca's under-sleeves render during sweeping.
- [x] Native v7 capture: the affected trader's sleeves and arms render.
- [x] Native v7 inspection catches remaining bodice clipping; v8 corrects coverage.
- [x] Native v8 capture: trader arms remain visible and the large bodice skin patches are removed.
- [x] Native v8 capture: Father's coat and under-sleeves are present (front/animated seam coverage remains limited).
- [x] 342 focused tests pass: 87 clothing, 242 character, 8 material-effects and 5 appearance-upgrade tests.
- [ ] Review the trader's remaining nine bind-pose intersection vertices; do not certify all poses from the front screenshot.
- [x] Identify the olive long-sleeved outfit as `ska_woman9`, source `s2_cotte_007` / `#V_006`.
- [ ] Confirm the reported narrow silhouette against a native source-game comparison; do not treat buffer repair as a proven proportion fix.
- [ ] Rebuild and verify the entire remaining wardrobe with contract 4.
- [ ] Separately resolve the reported missing gate guards.

The native run used the public Modding Tools 1.5.5 executable in a window and
a fresh Skalitz new game. It did not load an existing save. Live attachment
inspection confirms `outfit_torso.skin` for Bianca and the trader. Useful native
captures are `native-regions-v7-20261025/runtime/bianca-before.png`,
`native-regions-v7-20261025/runtime/bianca-after-close.png`, and
`native-regions-v8-20261025/runtime/trader-front.png` under the report root.
The engine no longer reports duplicate feature slots for the composed outfits.
The installed candidate rebuilds these four people only; the shared converter
applies the correction to subsequent source appearance builds. This report
does not imply that every already-installed NPC has been rebuilt.

Earlier workbench screenshots and passing geometry counts are historical
evidence, not native visual approval of this package.

## Additional native candidate, 26 October

`reports/clothing-lab/native-regions-v9-20261026/` extends the installed v8
package by rebuilding `ska_woman9` and `ska_woman6` through the same converter.
The other four rebuilt fixtures remain installed. Its 16 new mesh files have
matching render/internal RGBA. The olive outfit passes the bind-pose geometry
checks; the layered green outfit retains the same nine conservative skin
intersection findings as the trader. These are reviews, not visual approvals.

The old olive dress had a blue material-feature byte mismatch at all 1,319
vertices (render 224, internal 255). Its alpha hiding bytes agreed. The repair
therefore corrects material-feature input, not a demonstrated dimensional shrink.
The selected source variant is `#V_006`, no `A_Shrink` is applied to the dress,
and all garment vertex positions are unchanged by this rebuild. Existing rig
fitting narrows measured torso sections by roughly 2–6 percent relative to the
source variant; a render-buffer repair alone cannot certify that silhouette.
No per-NPC or per-dress widening has been added.

The v9 native test used another fresh Skalitz game in the public Modding Tools
(35.13 seconds level load). Reflection selected `outfit_torso.skin` for
`ska_woman6` and the original single-garment `part3.skin` for `ska_woman9`.
The layered outfit's inner sleeves/hands rendered, and the olive outfit
rendered during sweeping. Captures are `runtime/woman6-sleeves.png` and
`runtime/woman9-sweeping.png`; both are limited by interior lighting and are
not silhouette approval. Fly mode was disabled and AI updates restored after
testing. The remaining proportion and seam reviews stay open.

## Attachment and masking audit, 26 October

The additional screenshots exposed a separate missing dependency. KCD1's
`armor2clothing_attachment` table links armour to separate skinned details.
Resolving only `armor.clothing_id` and `armor.clothing2_id` omits these pieces.
Father's source outfit has separate apron bands/knot, sleeves and belts;
other source outfits use the same relationship for collars and dress details.

The general importer now joins those details into their owning garment before
native region composition, with source gender, tint, material and every LOD.
No extra inventory slot or named-character exception is introduced. Equivalent
material slots are deduplicated without changing faces or surface bindings.
Older 260-byte version-0x801 mesh headers are normalized to the standard header
only when their reserved extension is zero; unknown layouts still fail.

Male fitted-underlayer masks now protect exposed triangles sharing a covered
vertex, rather than erasing the whole triangle. Conditional opaque coverage also
includes head, hair and beard, so hats and hoods can hide covered hair while
removing the headwear restores it. The lab's validation contract is now 5, with
culled-garment and unmasked-hair review diagnostics. These are normal-ray tests
in the bind pose, not native animation certification.

The rebuilt Father workbench capture in
`outputs/father-final-20261026/workbench-front.png` shows his source apron straps
and belt, restored shoulder fabric, and cap. The installed-package comparison
is `outputs/father-comparison-20261026/workbench-front.png`. Both are developer
renderer captures. This audit does not establish that the reported narrow dress
silhouette is corrected; source variant, rig fitting and a native comparison
remain separate evidence requirements.

The completed developer audit is
`outputs/clothing-complete-audit-20261026/index.html`, with exact selections in
`checklist.csv` and detailed findings in `converted-outfits.json`.

- [x] Rebuild all 61 imported outfits: 20 female and 41 male.
- [x] Restore 121 source clothing details across 56 outfits.
- [x] Remove duplicate legacy appearance registrations; no catalog errors.
- [x] Reduce structurally failing outfit cases from 55 to zero.
- [x] Enumerate all 5,025 source soul records; 4,964 are not converted in this package.
- [x] Capture Father, Bianca, the olive dress, the green-dress trader and male layered outfits in the developer renderer.
- [x] Run 2,176 regression tests successfully, with 17 skipped.
- [ ] Resolve or validate the remaining 58 outfit review cases; three pass every current bind-pose check.
- [ ] Verify the candidate in native animation, including narrow dress silhouettes and hair through headwear.

The batch preserved all unresolved layer and geometry findings. That 61-person
overlay was subsequently promoted to a full package and installed for testing.
The full-package upgrader also retires owned character paths from legacy shared
asset archives before writing per-person shards; unrelated assets and recovery
hardlinks are preserved. This prevents old and new clothing assets from competing
under the same virtual path during a later installation.

### Native regression found after the whole-cast installation

The native test invalidated the earlier mesh-only success boundary. The shared
table migration matched a namespace prefix and removed 59 gameplay inventory
presets: 57 equipped/carried wrappers and two shop-stock presets. Consequently,
57 of the 61 imported NPCs could not reach their clothing preset. Four retained
working equipment chains. The native log reported missing inventory presets
and souls without clothing presets; the screenshots show fallback underwear.

Migration now removes only exact definitions supplied in replacement appearance
tables. Recovery restores missing inventory definitions without replacing newer
definitions or duplicating relocated appearance presets. Validation contract 6
checks actual equipment references, item GUIDs and clothing components. The
pre-regression recovery has 61 valid equipment chains, whereas the faulty
installed package has 57 failures. This regression must stay in the fixtures.

The exposed bodies revealed a second conversion problem: permanently baked
outfit fitting. Father's body was displaced by up to 38.5 mm and Mother's by up
to 68.4 mm relative to source shape after the shared rig conversion. Persistent
bodies now omit outfit-dependent `A_Shrink`/`A_Chest` fitting; inner garments keep
their fitting rules. Body coverage is regenerated against registered garments
and remains conditional on equipped hiding groups. The developer body repair
preserves topology, texture coordinates and skinning identities while retaining
installed material bindings. These structural checks are not native visual
acceptance; animation, neck joins, silhouettes and removal of clothing still
require an in-game comparison.

### Normal Setup and Resume (0.3.23 / recipe 60)

The normal finalizer now runs `setup_source_appearances` for both fresh builds
and saved-package upgrades. New source appearances carry a converter-policy
version in their PersonImport receipts. Resume rebuilds only older source
appearances using the same converter, including body-shape preservation,
attachments, material conversion and conditional coverage. It preserves
gameplay inventory wrappers and validates each actual equipment chain. A package
without that validation cannot be installed as a current recipe. Upgrades run
inside the existing private-package transaction and do not rebuild completed
world or lighting data. Developer workbench controls remain outside Setup.

The retail container pass also covers native material-only clothing roles:
when a role has Features but no mesh Elements, its native RPG occupancy supplies
the converted source mesh's region/layer. Authored visual attachments take
priority over occupancy: a caparison attaches to `horse_torso` even though its
inventory occupancy includes several horse regions. Ambiguous layers still
fail conversion. This resolves the three leg-armour and one caparison failures
from the 26 October build without per-item GUID exceptions.

Verification: all 589 required loot dependencies converted against local retail
assets with no unresolved items. A private copy of the failed package upgraded
its three source-person receipts, passed package hashes and equipment-reference
validation, and rebuilt zero people on the second pass. This is conversion and
package evidence; it does not establish native visual acceptance or a complete
new retail build.

### Neckline visibility (0.3.26 / recipe 61)

The 26 October male-collar and female-neck screenshots were reproduced using
the installed assets in the textured developer renderer. Disabling only the
Head component's hiding mask restored the missing surface. The source head
includes neck/chest geometry; the previous shared coverage test accepted
garment intersections up to 6 cm behind that skin. A normal ray could also hit
the edge of an open collar. Neither establishes that exposed neck skin is
hidden by clothing.

Head coverage now requires opaque outward intersections along the surface
normal and four surrounding 45-degree directions, followed by the existing
shared-vertex and triangle-boundary checks. This applies by component type to
both converters and every authored LOD. It contains no character-name,
garment-name, neck-height or hand-edited vertex exceptions. Covered head areas
can still be hidden by equipped clothing; removing that clothing restores them.
Body and hair coverage retain their separate existing policies.

The workbench now reports `hidden_head_without_exterior_cover` during geometry
validation. Regression fixtures exercise inward collars, open edges, alpha
cutouts, opaque covers, rotated surfaces and invalid normals. Appearance policy
2 and recipe 61 make normal fresh builds and saved-package upgrades use the
same fix. Completed world and lighting work is retained by the normal upgrade
transaction. The executable is built locally as Setup 0.3.26; this is not a
claim that an earlier published release contains the change.

Evidence is under `outputs/neck-seams-20261026`. Preview comparisons and
bind-pose coverage checks do not certify native animation, all clothing
combinations or remaining unrelated clipping issues.

### Saved actors with carried equipment (0.3.27)

The retail Resume test exposed a migration mismatch: `write_actor_paks` can put
converted carried-item models and streamed textures in the same numbered PAKs
as an NPC's appearance. The upgrade ownership check previously accepted only
the character appearance namespace and rejected these legitimate mixed packs.

The upgrader now explicitly recognizes the same actor's generated carried-item
namespace. Before replacing or retiring any numbered appearance shard, it copies
those equipment members into separate bounded ZIP32 PAKs, preserving their
virtual paths and payload bytes. Their table and inventory references remain
valid. Other namespaces and unsafe paths are still rejected. Both normal Setup
and the developer full-package upgrader use this preservation step; all writes
remain in private staging until the surrounding transaction succeeds.

Validation against the failed saved build: all 61 source-appearance archive
families passed the corrected ownership scan (124 carried-equipment members).
The exact failing actor, `rat_guardJanek`, passed normal appearance migration in
a bounded private fixture using its real saved tables, shards and source assets.
All 46 carried-equipment payload hashes were unchanged, its equipment chain
validated, original archive hardlinks were unchanged, and a repeat upgrade
rebuilt no actors. The full suite ran 2,231 tests with 17 skips and no failures;
the packaged 0.3.27 self-test passed. The user's saved job and installation were
not replaced by this validation run. Full Resume remains the next native-package
integration check.

### Fresh Setup build performance (0.3.28)

The optimization is in the normal source-appearance converter and initial
`build_quest_people_test` importer. It does not depend on a saved job or reuse
already-converted appearances. Appearance policy 2 remains unchanged because
the rendering contract is unchanged.

* Native materials share identical generated macro textures within an actor,
  rather than copying and packing the same pixels under each region's name.
  Bake keys include only inputs read by the pixel conversion; tint, mask zones,
  alpha, dimensions and relevant specular pixels remain distinct.
* The actor archive writer follows final character component dependencies.
  Conversion-only source materials/textures are not packed; every selected
  material slot, model, authored LOD and streamed texture/alpha slice is kept.
  Carried equipment and other namespaces retain their original writer behavior.
  Unknown/additive definitions also retain the original complete-file behavior.
* A fresh people-import batch shares read-only source tables and native archive
  indexes. The context closes its handles at completion or failure and cannot
  reuse stale inputs across jobs. Decoded image reuse is bounded to 128 MiB per
  material conversion; opacity reuse ends with that actor's coverage pass.
* Mesh conversion skips audit-only hashes and weight summaries, while retaining
  stream, finite-position, render-index and active-bone validation. Garment
  assembly checks each used joint's bind matrix once per input. Coverage uses
  per-segment bounds before the same intersection/alpha test and tolerances.

`tools/benchmark_character_build.py` builds new appearance directories, invokes
the production converter and ZIP32 writer, and records resolution, conversion,
packing, output sizes and per-phase timings. Each run starts with empty
converter caches. OS file-cache state is not reset. SHA256 manifests and material
surface manifests verify selected bytes and bindings. Verification reads and
temporary cleanup are included in harness wall time but not conversion/packing
time. Weapons, world registration, map conversion, lighting and installation
are outside this benchmark.

Evidence: `outputs/clothing-speed-20261026`. The comparison uses an extracted
`fd2a016` tools snapshot (`before-isolated`) and the first six actors of the
fresh 61-person build (`fresh-cast`), with no concurrent Setup installation.
The same six actors take 89.16 seconds before and 63.23 seconds after for
conversion plus packing: 29.1% less time. Packed bytes fall from 9,025,278,823 to
5,556,599,407 (38.4% less). No texture resolution, LOD or mesh-quality reduction
is involved. The complete-cast summary and final test results are recorded
alongside the benchmark reports. These are build-performance measurements,
not a new native-game visual acceptance claim or a full-map timing result.

#### Single-person investigation: Father

The subsequent audit follows `ska_fatherOfHenry` through the fresh Setup path,
not `upgrade_source_appearances`. `profile_population_upgrade.discover` supplies
placed identities to `build_quest_people_test.conversion_person`; explicit-actor
tools instead use `character_person.resolve_person`. The appearance path is:

1. `character_instance.effective_soul` applies placed-instance overrides;
   `build_npc_probe.resolve_parts` resolves body, head, hair and worn armour.
2. `static_assets.asset_pack` indexes the installed archives and stages source
   meshes, material permutations and complete texture families. Its mesh reader
   also resolves embedded material names, even when the character supplies an
   explicit material override.
3. `OutfitFitting`, `resolve_variant`, `align_internal_translation`,
   `upgrade_skin` and `limit_skin_influences` retain source shapes, apply authored
   fitting, change the joint bindings and produce native four-weight skins.
   `character_lods` applies and validates the same policy at authored LODs.
4. `attach_source_details` adds armour-linked belts, sleeves, knots and bands.
   `canonicalize_skin_skeletons`, `split_skin_regions` and `convert_fixed_outfit`
   combine the selected clothing into the native equipment regions.
5. `convert_character_materials` decodes source textures, supplies native
   material parameters, bakes float macro maps and writes blood/grime masks.
   `Bakes` owns worker scheduling, byte-identical bake reuse and completion.
6. `register_fixed_body_coverage` uses converted alpha, geometry and outward
   neck/head rays to write conditional visibility to both skinning buffers.
7. Native component/item/preset definitions refer to the result.
   `runtime_paths`, `generated_entries`, `checked_entries` and `write_actor_paks`
   select dependencies, generate streaming mip slices, validate and pack ZIP32.

The surrounding importer also stages carried equipment with
`character_weapon_inventory.stage`, generates soul/Storm resources with
`actor_resources`, registers the entity and binds world/home/scheduler data.
Those paths were inspected but are not included in the appearance timing.

Father resolves ten main parts and five linked clothing details. The trace
contains 15 final skin/LOD files, nine material files and 42 material slots.
Thirteen slots are unused across all of their selected meshes/LODs. This is
redundant processing but not the principal texture-size problem: their large
maps are also referenced by used slots. Only 2,496 staged texture bytes are
exclusive to those unused slots.

The pre-mask-sharing trace reports 525 chunk reads, 138 `Skin` constructions,
142 `CompiledSkin` constructions and 56 macro-bake requests, with 36 bake reuses.
These counts identify repeated work; profiler cumulative times overlap with
background bake waits, so use the converter's wall-clock phase timings for
performance totals. Region splitting retains complete source vertex buffers
to preserve morph/remap correctness: 82,514 vertices are stored across the final
families, of which 61,004 are referenced by draw indices. Geometry totals only
16.2 MiB; textures dominate this package.

The audit found and removed another concrete duplication: the effect-mask
generator was outside macro-bake reuse. It now shares one mask per immutable
source decal and shader channel policy, inside one conversion. Cloth's blue=192
and skin's blue=0 remain separate; item tint does not affect these masks.
Father's packed output falls from 861,628,030 to 705,014,686 bytes (149.36 MiB
removed). All 15 skin/LOD payloads are byte-identical and all nine material
surface manifests resolve to identical parameters and texture-family bytes.
The follow-up six-actor check (Father, Mother, Janek, Blanka, guard2 and woman5)
also passes: all 77 skin/LOD payloads and every retained nonmaterial asset are
unchanged, and every material surface manifest matches. The checks ran in two
batches and are equivalence evidence, not one combined timing benchmark.

A fresh, unprofiled Father run after this change measured 9.55 s conversion and
1.93 s packing (11.48 s), plus 2.68 s explicit identity resolution. Conversion
includes 2.31 s first-character source/index setup, which is shared by subsequent
people. Remaining conversion phases include 2.62 s native material/texture work
and 2.06 s body/hair/head coverage. These are single-machine observations, not
an irreducible conversion time or a full Setup estimate. The full test suite
passes 2,241 tests with 17 skips.

There is still substantial architectural waste. Of Father's remaining
687,833,588 packaged texture bytes, 538,933,064 (78.35%) have byte-identical
payloads in at least one other actor from the fresh 61-person run. The current
within-process bake cache can save computation, but each actor still copies and
packs its own namespaced files. A larger improvement requires batch-owned,
immutable shared asset dependencies, plus reuse of validated intermediate mesh
representations. Those changes are not implemented by the effect-mask fix.
They must retain package dependency ownership, cancellation and standalone
actor behavior; increasing worker count or reducing texture precision does not
address the repeated data.

The completed 61-person benchmark before effect-mask sharing took 361.29 s of
conversion and 102.57 s of packing, producing 37.55 GiB. Its 632.92 s harness
wall time additionally includes 135.08 s of explicit per-person resolution,
verification reads and cleanup. Normal profile discovery already resolves the
roster as a batch. Do not present either harness wall time or clothing-only
time as a measured full retail Setup build.

Private evidence: `outputs/clothing-speed-20261026/father-trace/` contains phase
timings, the profile, material-slot/geometry redundancy and cross-actor payload
comparison; `father-mask-sharing/` contains the new independent build manifests.
