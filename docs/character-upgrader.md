# Male and female character upgrader

`tools/upgrade_npc_characters.py` upgrades already registered KDC1 character
packages against the matching rig in the installed KDC2 game. It reads gender
and source garment metadata from `npc-report.json`; the conversion contains no
Martin- or mother-specific branches. The comparison that motivated it is in
[the character body audit](character-body-audit.md).

This is a development conversion of compiled character assets. It does not
replace the retail installer, finish the campaign, or certify all animations,
ragdolls and equipment changes.

## What changes

- The default keeps the original model-space geometry and proportions while
  rebinding each mesh's joint palette to the native rig. This keeps the source
  head, body and clothing boundaries together instead of independently resizing
  those surfaces to the target's spine spacing. UVs, source triangles and
  vertex-colour RGB channels are retained.
- Native packing keeps the strongest four joint influences and normalizes
  their byte weights to exactly 255. Existing four-weight meshes pass through
  unchanged; eight-weight inputs retain their original files and receive a
  report of discarded weight. This reduction is lossy, not a claim that every
  original influence survives. Duplicate joint entries are merged first.
- Explicit `--geometry-mode repose` applies each mesh's target/source bind
  transform blend to render/internal positions, normals, tangent frames,
  retained morph deltas and bounds. This alternative separated clothing seams
  in the live parent test and is not the default character-upgrade policy.
- The axial mapping compares the source and target spine chains. Equivalent
  chains retain their names; the extra native female spine joint is handled
  without assuming that source `Spine3` means native `Spine3`. Cloth helpers
  absent from the target retain their local relationship to their parent.
- Source body geometry is retained. Male bodies are partitioned into native
  body slots; female bodies retain their complete source arm/leg mesh in the
  torso slot. The female head and clothing remain necessary parts of the
  complete appearance: her source base body does not contain a torso.
- Body/head vertex-alpha membership is cleared to prevent unrelated KDC2 hiding
  groups from discarding imported skin. Clothing and hair alpha is preserved.
  This is a compatibility policy, not a full translation of KDC1's dynamic
  hiding system. Covered male body regions follow the native layer selection;
  the female limb mesh stays visible because all limbs share its torso slot.
- Selected source clothing variants are baked once. Both the old float morph
  format and the compressed format are supported. Existing material paths,
  blood/dirt/wear shader parameters and colour settings are retained.
- Source underwear is registered with real layer-8 elements. An empty clothing
  record is not accepted by the native underwear selector. If there is no
  source underwear piece, the previous default is retained.

The optional, experimental `--compose-outfit` builds a fixed initial outfit using the existing
region-assembly converter. It unifies joint palettes case-insensitively,
including `FV_*` / `fv_*` aliases that otherwise cause a duplicate-controller
fatal error. The final configured torso item's **existing ID and attributes**
are retained; only its clothing-component binding changes. Other item records,
inventory rules and preset item IDs remain intact.

For this fixed composition, the `p` and `l` fields in authored source mesh names
identify part and layer. An inner garment with a higher layer on the same part
uses its supplied `#A_Shrink` morph at weight 1, along with its selected variant.
This is an explicit initial-outfit fitting policy, not a reconstruction of all
source runtime fitting logic. Assets without those identifiers or an authored
fit shape need further fitting work. Underwear surfaces are also included in
the final composition because a final outer layer suppresses its separate draw.

The fixed display shares its owning item's runtime condition. Independent
removal, replacement, blood/dirt/wear per garment and changing body shapes
require rebuilding the composition. Existing assembled outfits retain their
materials and features; merging individual materials that contain native
top-level feature bindings is refused rather than dropping that metadata.

## Stage, inspect and install

Install Python dependencies from `requirements-npc-effects.txt`. Use a new
output directory and a new asset revision, and close the development game
before installing.

```powershell
python tools/upgrade_npc_characters.py `
  --data 'D:\SteamLibrary\steamapps\common\KCD2Mod\Data' `
  --native-game 'D:\SteamLibrary\steamapps\common\KingdomComeDeliverance2' `
  --namespace martin_v1 --namespace campaign_mother_v1 `
  --revision body_upgrade_v8 `
  --output outputs/character-upgrade-v8

python tools/upgrade_npc_characters.py `
  --output outputs/character-upgrade-v8 --install
```

The staging directory contains the generated `Data/` tree and
`upgrade-plan.json`: source and output hashes, original-material bindings,
gender/rig provenance, per-part geometry and influence checks, baked morphs, converted
regions and the exact list of files to install. Staging does not alter the game.

Installation validates every source and target again, backs up each replaced
file, writes the revision, and verifies installed hashes. A copy failure restores
already touched files, including a partially written current file. A semantic
guard checks that table records outside the selected characters are unchanged.
The five overlays are CharacterComponent, ClothingConfig, clothing_preset,
item__gluenpc and the GlueMapper Storm appearance rules. World/map packages,
quests, scripts, CDFs, original skins and material files are not rewritten.

Restart the game to load the new records. Confirm the actual meshes in
`/api/ent/ClothingSystem/AttachmentManagersByName/<actor>?depth=4` and inspect
the characters visually. A manager's `Show`/`Streaming` status alone is not
proof that a surface renders correctly.

```powershell
python tools/upgrade_npc_characters.py `
  --output outputs/character-upgrade-v8 --rollback
```

Rollback refuses to overwrite later changes and verifies backup hashes. To
repeat an upgrade from these original registered inputs, roll back the prior
revision first, then stage into a new directory. Source garment morphs have
already been baked in upgraded files, so reapplying them is intentionally
rejected.

## Reusable conversion API

```python
from character_skin_upgrade import upgrade_skin, limit_skin_influences, canonicalize_skin_skeletons

converted, evidence = upgrade_skin(
    source_skin_bytes,
    native_skeleton_bytes,
    variant=selected_source_variant,  # None for body/head or default garments
    geometry_mode="preserve",         # keep the source body and clothing shape
)
converted, influence_evidence = limit_skin_influences(converted)
compatible_garments = canonicalize_skin_skeletons(converted_garment_bytes)
```

Callers can explicitly supply `morph_weights={"#A_Shrink": weight}` when fitting
an authored source garment. Applying a full shrink to every covered garment
caused interpenetration in the parent outfit test; the default individual-item
upgrade does not apply that heuristic.

These functions perform no filesystem writes. Unsupported or inconsistent
layouts fail explicitly. `tests/test_character_upgrade.py` covers bind-space
transfer, normals and morphs, eight weights, vertex features, spine mapping,
controller aliases, source fitting, both genders, existing materials and item
IDs, installation failure and rollback. The full test suite uses synthetic
fixtures and does not need proprietary game assets.

## Live development validation

The `body_upgrade_v8` test installed both source parents into `KCD2Mod` and
loaded `kcd1_population_v1` afresh. The mother's arms/hands are visible, the
collapsed neck is gone in the captured idle view, and Martin's original tunic
and body render together. The selected meshes are also recorded from the live
attachment managers. The 26 installed output hashes match the staging plan.

The decisive neck comparison was between v7 and v8: both preserved the source
shape, but v8 packed the mother's head into four normalized influences. Her
source head has 1,048 vertices with additional weights, mostly around the neck.
The greatest discarded weight was 32/255 before renormalization. The native
`Shaders/HWScripts/CryFX/ModificatorVT.cfi` gates its extra vertex weights behind
`WH_ENABLE_8_WEIGHTS_SKINNING`, whose definition is commented out in the inspected
retail source. The compute shader has a separate variable-count path; this test
does not claim a universal eight-weight limitation across every rendering path.

Local evidence is in `outputs/character-upgrade-v8/`: the plan, backups and
`live-after/` screenshots/manager captures. These are ignored development assets,
not files distributed with the converter. Walking, combat, facial animation,
ragdolls and arbitrary changes of clothing still need separate validation.
