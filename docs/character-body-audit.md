# KDC1 / KDC2 character assembly comparison

Read-only investigation captured on 2026-10-08. This records the current
missing-arms and neck/shoulder investigation; it is not a verified body fix.

## Captured evidence

The local dump is `outputs/character-compare-006/`:

- `comparison.json`: 73 original asset records and 16 installed mesh records,
  with archive paths, hashes, mesh streams, subsets, vertex colours, bone
  weights and bind matrices. Original reference selection includes both
  generic bodies/heads and KDC2's `m_head_father` / `f_head_mother` assets.
- `raw/`: original meshes, materials, rigs and shader sources from the user's
  two retail installations. There are 39 OBJ exports of authored geometry.
- `installed/`: snapshots of the current converted skins, materials, CDFs,
  component tables and Storm rules, separately from the originals.
- `live/`: clothing-manager XML read **after the level loaded** for
  `ska_fatherOfHenry`, `ska_motherOfHenry` and `Dude`.
- `findings.json`: measured rig differences, source/installed chunk comparison,
  selected garment variant measurements and hiding-mask evaluation.
- `body-head-comparison.png`: four columns showing original male and female
  body/head geometry from each game, plus neck/shoulder detail.

The loaded parents are imported KDC1 characters running in KDC2. `Dude` provides
a loaded native male component reference. No separate native female actor or
KDC1 runtime was started for this capture: those two reference assemblies come
from the original installed archives. The image is a static mesh render, not a
game screenshot. It applies no morphs, animation, material transparency or
hiding masks. Geometry bounds alone do not establish an animated seam defect.

Game definitions and installed assets were not changed during this audit.
The previously attempted full-body registration and `KeepBodyLayer` changes
remain experimental; the user reported that arms were still missing.

## Confirmed differences

| Area | KDC1 sample | Native KDC2 reference | Import implication |
|---|---|---|---|
| Male base body | One `s1_body_npc.skin`, 2,786 vertices / 4,698 triangles | Five authored body elements: torso, arms, legs, hands and feet | A universal torso-only registration is not native male assembly. |
| Female base body | One `s2_body.skin`, 2,096 vertices / 3,464 triangles; exposed arms and lower legs, without a torso | One `female_body_01.skin` in the torso slot, 9,557 vertices / 16,588 triangles, with a fuller body | The entire KDC1 appearance requires its head and clothing pieces as well as the base body. |
| Head boundary | Father's head includes a low V-shaped chest section; mother's head includes a chest section | Both native parent heads terminate higher and meet their native torsos | KDC1 heads cannot be assumed to meet native KDC2 body cuts. |
| Female body vertex alpha | All 2,096 vertices have alpha 0 | Region-specific alpha values, including 255 for vertices outside hiding groups | The byte has to be translated into native visibility semantics. |
| Male body vertex alpha | No colour stream in the source body | Per-region native colour streams | Its missing-stream fallback cannot be inferred from the female case. |
| Female rig | Neck and shoulders descend from `Spine3`; no `Spine4` | Neck and shoulders descend from `Spine4` | A name-only mapping does not preserve the source female rig geometry. |
| Male rig | Source main-body transforms | Main spine, neck, shoulder, arm and hand matrices match within floating-point noise | The female correction must not be blindly applied to males. |
| Selected garment shapes | Source rows request variants such as `#V_004` and `#V_007` | Native uses registered component morphs / authored meshes | Current individual-garment registration preserves raw meshes but does not activate or bake those source variants. |

Raw head Z bounds (metres in each authored mesh's coordinates):

| Head | KDC1 | KDC2 |
|---|---:|---:|
| Father | 1.2568–1.7307 | 1.4019–1.7298 |
| Mother | 1.2150–1.6342 | 1.3061–1.6279 |

These are different model boundaries, not instructions to move the head by the
bound difference. The rendered comparison makes the different cuts visible.

## Why the mother's arms can disappear

The loaded mother's manager explicitly selects
`Glue_campaign_mother_v1_0` with `part0.skin` in the torso body layer. That
installed mesh matches the original KDC1 body chunk-for-chunk, including the
arm geometry and its all-zero alpha channel. The manager reports
`HidingGroups="FemaleUnderwear Pouch"`.

The native hiding-group table assigns `FemaleUnderwear` to torso bit 7 and
`Pouch` to torso bit 2. KDC2's `HumanSkinValidations.cfi` enables hiding groups
for HumanSkin, and `WHHidingGroups.cfi` inverts the vertex-alpha byte before
testing it against the active mask. The relevant development shader sources
hash-identically to retail KDC2's sources.

Evaluating that shader predicate with the loaded group names gives:

| Body | Vertices matching the discard predicate |
|---|---:|
| Imported female body (alpha 0 everywhere) | **2,096 / 2,096** |
| Native female body with the same torso mask | 1,865 / 9,557 |

This is a concrete mask-encoding conflict. `KeepBodyLayer=true` preserves the
body element in the manager but does not override per-vertex shader hiding.
It explains why selecting the source mesh alone did not restore the arms.
The audit captured the manager and evaluated the shader predicate; it did not
capture GPU constants or draw calls, or apply a fix and prove visual recovery.

## Loaded clothing is also different from the source outfit

The mother's manager contains native `f_underwear01_m08`, which activates
`FemaleUnderwear`. Her KDC1 outer garment `Glue_campaign_mother_v1_5` is selected,
but the KDC1 underdress `Glue_campaign_mother_v1_3` is absent from the loaded
slot cache. The generated definitions assign both source dresses the same
`F_SimpleDress` role. The intended source underdress has therefore not become
the loaded underwear component.

Both loaded parents report an empty `Morphs` list. The installed individual
garment meshes retain the original source chunks, so their variants are not
baked either. The existing assembled-outfit path does bake selected variants;
the individual-garment path currently just records their names in its report.

Measured source variant position changes include:

| Garment | Source variant | Largest displacement | Largest displacement above Z=1.2 m |
|---|---|---:|---:|
| Mother's outer dress | `#V_004` | 94.64 mm | 29.13 mm |
| Father's coat | `#V_007` | 45.87 mm | 31.85 mm |
| Father's shirt | `#V_001` | 61.56 mm | 32.35 mm |

These figures decode source shape deltas; they do not measure the live gap.
The source female boots also request a variant, but use a format the existing
variant decoder does not support. That measurement remains explicitly failed
in `findings.json`, rather than silently treating it as a zero displacement.

## Rig and skin-weight differences

The original male `.chr` has 266 bones and native male has 243. The female
counts are 255 and 248. These totals include controls and auxiliary bones, so
the totals themselves are not a skinning compatibility test.

For the corresponding female joints, in model-space bind coordinates:

| Joint | Native minus source position, mm (X, Y, Z) | Bind-axis rotation difference |
|---|---:|---:|
| Neck | +0.19, −18.17, −23.63 | 4.88° |
| Left shoulder | +9.10, +14.27, +2.39 | 18.64° |
| Left forearm | +2.91, −58.87, −26.07 | 16.31° |
| Left hand | +25.12, −38.79, +19.90 | 87.54° |
| Right hand | −24.88, −38.80, +19.90 | 87.54° |

These explain why female animation compatibility needs a real bind-space and
hierarchy conversion. They are not evidence that replacing matrix bytes alone
is sufficient. The matching male joints do not have these differences.

The mother's source head has 10,245 vertices, and 1,048 use nonzero influences
in the second block of bone weights. The combined weights sum to 255 for every
vertex. A converter must preserve both blocks. The source dresses also use
weighted `v_*` cloth bones absent from the native base skeleton. That requires
checking attachment skeleton extension / cloth binding; absence from the base
skeleton alone does not prove those weights are dropped at runtime.

## Work required before claiming the appearance is fixed

- Translate body hiding masks, keeping exposed KDC1 limbs visible while
  retaining appropriate covered-body hiding. Verify a fresh-load manager dump
  and the actual arms before extending the change.
- Give the KDC1 underdress its intended underwear role, instead of relying on
  native underwear and two competing dress roles. Validate selected slots.
- Bake or register the selected source garment shapes in the individual-item
  path; preserve their positions, skin weights, internal vertices and bounds.
- Handle the male and female assembly rules independently. Keep the original
  KDC1 head/body/clothing boundaries together when adapting them.
- Implement and validate female bind-space / hierarchy conversion with all
  influences and the cloth attachment bones. Check shoulders, wrists, idle,
  movement and ragdoll, not only a static pose.
- Compare source head/body material tint and native skin parameters separately
  from geometry once the pieces are visible and correctly assembled.

The audit identifies concrete differences and missing conversion steps. It
does not yet prove which combination fully resolves the animated neck seam.

## Repeat the read-only dump

```powershell
.venv/Scripts/python.exe tools/audit_character_bodies.py `
  --kcd1 D:/SteamLibrary/steamapps/common/KingdomComeDeliverance `
  --kcd2 D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2 `
  --development D:/SteamLibrary/steamapps/common/KCD2Mod `
  --output outputs/character-compare-next --live

.venv/Scripts/python.exe tools/render_character_comparison.py outputs/character-compare-next
```

Use a new output directory. `--live` issues read-only GETs to the development
game's local HTTP API; omit it for an archive-only comparison. Selection of the
KDC1 parent outfits comes from the existing local NPC reports, while their
original bytes are read from the retail archives. The tool refuses ambiguous
base assets, resolves numeric patch precedence, and records inherited native
component definitions. It is a targeted parent/body audit, not a universal
character exporter. `findings.json` in the captured run is additional measured
analysis; the dump and renderer commands reproduce the raw comparison inputs.

All extracted game data stays in the ignored local output directory. The
repository contains only inspection tools, synthetic tests and this report.
