# Generic female appearance conversion

The person importer resolves the source instance's body, head, hair, clothing
preset, garment variants and materials. Conversion policies do not select an
NPC or quest by name. Quest adapters only select the source person to import.

## Corrections

* Female outfits retain separate source item identities, text, value and armour
  statistics. Native female equipment roles replace the previous single male
  coat template. Complete source dress meshes retain their skirts and sleeves.
* Covered inner garments use their authored shrink morph when available. The
  innermost source garment supplies the underwear geometry; the KCD1 female
  base mesh contains limbs rather than a complete replacement torso.
* Skin, hair and clothing use shipped KCD2 character shader configurations.
  Source surface textures and hair colour are preserved, and legacy fabric
  colourization is baked into the native clothing material path. Material
  records are merged into the patch named after the manifest's mod ID.
* Female bind-pose conversion uses a shared smooth field fitted to anatomical
  joint positions. Bone-local axes are not used as anatomical rotation angles:
  the two games use substantially different wrist/finger axis conventions.
  One field transforms skin, hair and clothing regardless of their different
  skin weights. Its Jacobian also transforms normals, tangents and morph deltas.
  Duplicate/degenerate landmarks and folding transformations fail explicitly.
* The existing male geometry and material path is unchanged.

Implementation: `character_person_appearance.py`, `character_source_clothing.py`,
`character_native_materials.py`, `character_landmark_warp.py` and the existing
`character_skin_upgrade.py` byte conversion utilities.

## Mod-tool evidence, 2026-10-10

Tested in the running KCD2Mod engine on the existing `kcd1_population_v1`
development level, before building the retail bundle. The tests used registered
source people with actual native clothing managers, not just standalone meshes:

* `rat_refugee_tereza`: visible face, brown hair, dark source dress, arms, hands
  and footwear. A native female control was spawned beside her. The engine
  accepted `relaxed_idle_both` for both once the imported character streamed in;
  close screenshots show the wrist and finger pose after conversion.
* `rato_bartender1`: a second source female with a different head, headwear and
  blue dress. Face, outfit and hands are visible at close range. Its explicit
  animation request returned false, so it is an appearance/normal-runtime-idle
  check, not evidence of the requested animation playing.
* A distance view recorded the two females, native control and male merchant.
  It is a visibility sample around ten metres, not exhaustive LOD coverage.
* Rebuilt `rato_innkeeper1` using the existing namespace: all 616 generated files
  matched the pre-change v31 merchant output byte for byte.
* Both female candidates' five database files passed the official
  `GeneratedDatabase.dll` reader. Automated checks include shared seam positions
  under differing skin weights, normal/morph conversion and patch suffixes.

Local evidence is under `outputs/female-import-v3/live/`, including the captured
native attachment-manager XML, close screenshots and the distance view.
`outputs/female-import-v3/male-regression.json` records the male comparison.
Source assets and screenshots remain local and are not distributed in Git.

The v33 retail test package was built after these live checks. All 14 packaged
database XML files passed the official reader, and the complete automated suite
passed 284 tests. The installed files were hash-verified, with their previous
versions saved in `outputs/region-travel-v33/installed-backup/`. World and
navigation archive entry contents are unchanged (container timestamps differ).
The original development map/table files were restored after capturing a
replayable snapshot in `outputs/female-import-v3/dev-tested-snapshot/`.

## Remaining verification boundaries

### Deferred: female upper-arm / biceps mismatch

User-reported retail evidence on 2026-10-10 shows an abrupt mismatch where the
bare upper arms meet the short sleeves on a prone female wearing the inner
garment. The current result is accepted for now; this remains an open visual
issue and is not a reason to change the current build without further work.

Possible cause: differing female body-size variants or upper-arm proportions.
That is a hypothesis, not a confirmed diagnosis; sleeve coverage, bind alignment
and deformation also need comparison against the source outfit. A future fix
must belong to the generic female upgrader, not an individual NPC override.
Local screenshot: `outputs/female-import-v3/live/retail-female-biceps-known-issue.png`.

Retail rendering requires a fresh launch and visual confirmation; the mod tools
can compile shaders and therefore cannot certify retail shader-cache behaviour.
Combat, all hand gestures, ragdolls, every LOD and changing arbitrary equipment
have not been exhaustively checked. Small garment/body intersections still need
outfit-specific visual review. Native item icons are retained; source icon-atlas
conversion is not implemented. Unsupported garment layouts and multi-component
items currently fail explicitly rather than silently choosing a male coat.
