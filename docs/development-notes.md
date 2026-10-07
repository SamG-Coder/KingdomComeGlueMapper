# KingdomComeGlueMapper

First objective: load and explore the Kingdom Come 1 world inside the Kingdom Come 2 runtime, using the installed compiled game packages. Preserve the original layout. Buildings, vegetation, original materials, NPCs and quests require further conversion.

## Status — 2026-10-07

The compiled KCD1 `rataje` terrain has loaded in the KCD2 development runtime. Earlier conversions rendered severe terraces despite numerically correct height samples. A stripped native KCD2 `trosecko` control rendered smooth terrain, confirmed by the user's screenshot. That screenshot is not the KCD1 port.

Build `kcd1_terrain_v4` changed geometry-error mapping, but the user's screenshots confirmed severe terraces remained. It did not fix the main defect.

Build `kcd1_terrain_v5` corrects the actual height decode: the installed KCD1 samples use a fixed 0.05m height step, despite a legacy variable `fRange` in their headers. Interpreting those samples with the legacy range created neighbouring-sector jumps up to 46.2m. Using fixed-step heights reduces all 519,488 shared-boundary comparisons (including reduced-resolution sectors) to under 0.05m and matches full-resolution sector maximum bounds within that step. Original quantized heights, offsets and surface indices are now preserved; target float32 scaling differs by at most 0.000001405m from the fixed-step reference.

All 5,461 nodes and 17,139,968 height/surface samples pass checks. Native-style geometry-error layout is retained with a conservative maximum old error for internal nodes and zero at leaves. This is not a recomputation of the target metric. The user confirmed v5 visually: connected hills now render smoothly in KCD2. Pale gaps and texture tile boundaries remain visible and unresolved. Skalitz spawn and no-collision flight are confirmed in the runtime log. Neither numerical tests nor a successful load establish a complete port.

## Close-up materials — v6

The v5 probe assigned every terrain surface to KCD2's `underpaint_roads` placeholder, whose diffuse input is a white texture. It did not reference original terrain detail maps. This explains why approaching the terrain revealed plain blurry shading instead of the expected grass, soil and rock detail.

`kcd1_terrain_v6` restores all 26 original surface/material assignments under isolated `gluemapper/kcd1_terrain_v6/` paths. Its separate `KCD2Mod/Data/gluemapper_kcd1_terrain_v6.pak` contains 78 DDS texture families / 726 texture files (192 from installed HD packages), including streamed mip and alpha/gloss files. Base archives are overlaid with HD overrides. Original UV scales and material parameters are retained, numeric shader feature masks are removed so the target can resolve named features, and target terrain depth-blending defaults are added. Every texture payload is checksum-verified and all rewritten texture references resolve inside the package. The runtime did not auto-mount the arbitrary Data archive, so the builder also installs its contents as loose files under `KCD2Mod/Data/gluemapper/kcd1_terrain_v6/`. This isolated namespace avoids replacing official assets.

Build with `--original-materials`. This is a material compatibility experiment; successful packaging is not proof of visible high-resolution mip residency. The runtime retest resolves all 26 original material paths with no placeholder fallback or errors for the isolated assets; startup texture streaming completed. The user screenshot confirms distinct close-up soil and grass detail, replacing the blurry placeholder. Highest-mip residency has not been measured. Pale terrain gaps remain visible. The older v5 checkpoint is preserved, and no retail archive is overwritten.

## Launch the current test

Close the existing game, then from this project directory:

```powershell
python tools/launch_probe.py
```

Defaults:

- Level: `kcd1_terrain_v6` (converted KCD1 `rataje`).
- Location: Skalitz, X 734.89313, Y 3421.4497. This is original `TagPoint232` in `q_skalitz_questitems`, next to the opening mother/Henry dialogue cameras. It is an opening-area reference, not a verified exact bed spawn.
- Height: query the runtime terrain elevation and place the player 1m above it.
- No-collision fly mode: enabled through the game's `player.player:SetFlyMode(FlyMode_OnNoCollisions)` interface. Use `--walk` to disable it for collision testing.
- Intro controls: `g_skipIntro=1`, `sys_intromoviesduringinit=0`, `wh_sys_SkipStartupVideoWhenPossible=1`. The installed runtime recognizes these controls; visual confirmation that this launch skips all videos is still required.

The launcher waits for the requested level and player, then logs the spawn height and resulting flight mode. It uses the development runtime's localhost console API, refuses a duplicate game instance, and changes no retail configuration or save files. `--attach --level <current-level>` applies the spawn and flight settings to an already running test. Optional `--x`, `--y`, and `--clearance` override the location. Flight is developer player flight, not a separate cinematic camera.

Skalitz currently has terrain only: buildings, trees and road objects have not been converted. Original surface materials are being tested in v6.

## Build and verify

Python 3.10 or newer; standard library only. Both games and KCD2 Modding Tools must be installed under `D:\SteamLibrary\steamapps\common` unless paths are overridden.

```powershell
python tools/audit_maps.py
python tools/upgrade_map.py --original-materials --output 'D:\SteamLibrary\steamapps\common\KCD2Mod\Data\Levels\kcd1_terrain_v6'
python tools/verify_compiled_conversion.py --level kcd1_terrain_v6
```

The builder refuses to overwrite existing output. Use a new level name for another experiment. `reports/conversion-checks.json` records the latest independent sample and geometry-error checks. Each generated level has an `upgrade-report.json` with source hashes and resource references.

KCD2Mod's retail archive symlinks remain untouched. Each probe is a separate level directory. Earlier probes and the native `kcd2_control` remain available for comparison.

## Observed compiled format differences

| Field | KCD1 | KCD2 |
|---|---|---|
| Examined world | rataje | trosecko |
| Terrain package | level.pak | terrain.pak |
| Terrain version | 28 | 29 |
| Node version | 7 | 8 |
| Unit-size storage | int32 | float32 |
| Height/surface sample | uint16: 12 height bits, 4 surface bits | uint32: 12 height bits, 20 surface bits |
| Geometry error | six per-LOD values | first value used; native remaining five zero |
| Object octree version | 5 | 8 |
| Distant/object geometry | uberlods.xml and octree | hlods.dat, separate HLOD packages and octree |

The converter preserves terrain bounds and surface palette indices, rebuilds minimal runtime level/mission documents, copies the original terrain cover texture, and uses either a native target fallback surface material or the optional isolated original-material package. It deliberately emits no outdoor objects. Changing header versions alone is insufficient.

The archive reader accepts an observed KCD1 ZIP inconsistency where local-header paths use backslashes and central-directory paths use forward slashes. It accepts only separator differences and retains the ZIP reader's other checks.

The original editor test failed because `Level.editor_xml` was absent. That is an editor-source requirement, not a requirement for this compiled runtime conversion approach. Raw editor data is not an input to the current builder.

## Remaining acceptance checks

Confirm the converted terrain visually in KCD2, including Skalitz and off-diagonal locations; verify walking/collision separately from flight. The next priority is vegetation: first convert and visually validate a small patch near Skalitz, including placement, foliage materials and distance transitions, then expand to the original world layout. Buildings and other placed objects, remaining HLOD work, roads and water follow. NPCs, navigation and quests are later work. A terrain-only package is not a complete map port.

## Texture transition experiment — v7

The user observed abrupt grass/soil edges in v6. An audit of all 17,305,600 native KCD2 terrain samples found no nonzero secondary mixture weights, so the single-material sample representation alone is not evidence of lost blend masks. The installed target Terrain shader blends interpolated vertex alpha with the material height texture, BlendFactor and BlendFalloff.

`kcd1_terrain_v7` is an A/B experiment with `--original-materials --terrain-blend-factor 1`. It retains source texture inputs and UVs; its terrain.dat is byte-identical to v6. It changes material transition shading, not terrain paint assignments, and does not claim to reconstruct missing original masks. Visual comparison is pending. The default launcher remains on the confirmed v6 checkpoint until the experiment is accepted.

## Vegetation checkpoint - v11

The initial 19-instance probes loaded models but were not visible. Native KCD2
stores vegetation instances in its HLOD stream, and moving the instances there
produced visible vegetation. The v11 build contains 541,465 individual instances
across 226 mesh groups in 3,980 spatial sectors. The user screenshot confirms
nearby trees and distant forest. Terrain bytes remain identical to v6.

Expanded asset coverage exposed relative CGF material references; these now
resolve against the original mesh directory before namespacing. Texture semantic
suffixes are retained. All 226 vegetation models load with no isolated-asset
errors in the captured v11 log. All emitted HLOD block offsets, sizes and group
indices validate. Four asset-free binary tests pass.

This is not full vegetation coverage: unsupported source block remainders and
merged vegetation are not decoded. Billboard/proxy atlases, advanced wind,
collision and exhaustive LOD behaviour remain unverified. The captured view is
visual evidence for rendering, not proof of every instance or every distance.

## Visible world objects and shader recovery - v28

The complete mixed-object walker found 253,142 vegetation-format records beyond
the leading runs handled by the old reader. Including previously omitted
non-vegetation models as well produces all 1,483,225 individual source instances:
718,931 foliage placements plus instanced stones and clutter. Existing merged
sectors, terrain, water and building HLOD records are preserved. Source position,
scale and rotation bytes remain unchanged; the reported tree LOD misalignment is
not yet established as fixed.

The static prop pass adds 120,776 brush placements. The visual-entity pass resolves
pickable-item database models, shared mission entities, initial-state layer
entities and parent-local transforms. The v28 level contains 10,250 uniquely
named visual item entities. The user confirmed Henry's house is furnished.
These are visual imports, not working pickup, inventory or quest behavior.
Unsupported character models, later states, missing dependencies and unresolved
parents remain reported exclusions.

An experimental v27 pass added 2,408 level-local designer meshes. User captures
showed red blocker walls and giant helper planes; v28 restores the v26 geometry
set and retains only 31 additional resolved item materials. Designer geometry
is now excluded from the default prop pass pending classification.

The original opening quest links its player CutsceneSpot to EntityId 120819,
TagPoint231: position (733.87537, 3421.9014, 63.908913), quaternion
(0.60181504, 0, 0, -0.79863548). The earlier nearby TagPoint232 belongs to Henry's
mother. The launcher's `--kcd1-start` applies the player marker's height and
orientation with no-collision flight enabled. Walking collision is unverified.

Expanded rendering exposed two errors in the installed KCD2 shader includes:
the instanced variants lack the per-draw constants referenced by
`Get_BindlessBoneOffset_Prev` and `ShadowMotionBias`. A local generator reads the
user's installed shader source and emits two loose include overrides. The first
selects the instance-data buffer; the second retains pass motion bias with a
neutral multiplier of one, because the instance buffer lacks that coefficient.
No engine shader source or game assets are committed. The v28 runtime log showed
1,984 distinct compile entries, no repeated entries and zero compilation errors;
cache writes were enabled and shader editing disabled. User confirmed completion.

Validation: 27 asset-free tests pass; v28 contains 171,260 brush records,
1,483,225 individual instances, and 10,250 unique visual entities. Successful
compilation and structural counts do not establish exhaustive visual, collision,
LOD or streaming correctness.

### Tree shadow-proxy conversion candidate

The user reported a detailed tree and a coarse flat canopy drawn together.
For the inspected `tilia_cordata_b`, the target has one individual placement;
its base CGF contains both an atlas subset and a `shadow_proxy` subset. The
importer previously copied that proxy's ordinary Vegetation material unchanged.
Changing global LOD settings did not provide an acceptable correction and those
diagnostics were reverted.

`vegetation_materials.convert_shadow_proxies` now preserves material-slot order,
converts explicitly named Vegetation shadow proxies to Nodraw, and clears the
no-shadow flag on their visible Vegetation siblings. Thus visible geometry
provides shadows instead of the coarse proxy. Mesh files, placement transforms,
and LOD selection are unchanged. This can increase shadow-rendering cost.
`repair_tree_materials.py` applies the same conversion to existing loose generated
assets, retaining original files in a required backup directory. The first local
pass changed 78 materials. In-game confirmation of canopy appearance, shadows,
and distance transitions is still pending; this is not yet a verified LOD fix.

On 2026-10-08 the user confirmed that the flat canopy is still visible after
that material conversion. The inspected tree beside Henry's starting area is
`objects/vegetation/trees/tilia_cordata/tilia_cordata_b.cgf`, source group 388,
record offset 115915232, position (728.38635, 3400.63550, 64.23100).
It maps to target group 295, `glueveg/kcd1_groundcover_v13/mesh295.cgf`;
exactly one matching individual placement was found in the target HLOD stream.
The embedded material is `m36.mtl`; its shadow_proxy slot is already Nodraw.
The separately copied `mesh295_lod3.cgf` has 10 vertices and 8 triangles and
uses material slot 3 (`bblods`, BILLBOARD_ATLAS). The base mesh uses slots 4
and 1. This identifies the explicit flat LOD asset, but does not yet establish
which draw produces the unwanted canopy. The material candidate did not solve
the reported issue; importer/LOD investigation remains open.

### Overlapping forest stand-ins identified (v29)

The KCD2 asset comparison showed the same base / `_lod1` / `_lod2` / `_lod3`
tree family, with native render data split into `.cgfm` companions. Native
mesh headers also carry the valid face-area flag. These differences were not
changed in this fix: the overlapping tree came from a separate world proxy.

The v23 props pass imported 4,041 `objects/uber/` brushes as ordinary objects.
Beside Henry's house, `objects/uber/wh_modeluberlodnode_11_53.cgf` became
`gluebuild/kcd1_items_v23/mesh2934.cgf`. Its nodes include
`tilia_cordata_b001`; its bounds enclose the separately placed detailed tree.
Source `terrain/uberlods.xml` identifies this mesh as the FarObject of group
2714, centered at (736,3424,62.200001), with a 200m switching sphere and
NearObject vegetation cell 3403. The props importer omitted that relationship,
making the distant stand-in visible alongside the near vegetation. Native KCD2
instead associates far representations with its HLOD hierarchy and ProxyIndex.

The builder now excludes `objects/uber/` from ordinary brush imports and reports
the excluded paths. `remove_uberlod_proxies.py` repairs an existing level by
matching source proxy bounds and transforms exactly, rebuilding HLOD block
offsets/sizes, and preserving all retained records. The v29 build removes 4,041
brushes (171,260 -> 167,219), preserves all 1,483,225 individual vegetation
records byte-for-byte, and copies terrain.pak unchanged. The original 78 tree
materials were restored from their verified backups and the unsuccessful
shadow-proxy conversion was removed from both build paths. The standalone
shadow-material repair tool is historical, not the recommended correction.

Validation: 31 asset-free tests pass, including nested HLOD offsets, retained
records and distinguishing world proxies from legitimate per-tree LOD files.
The user confirmed on 2026-10-08 that v29 fixes the overlapping flat trees.
Legacy world-scale far stand-ins remain
excluded until their near/far switching graph is properly converted; distant
coverage and performance therefore still need testing.
