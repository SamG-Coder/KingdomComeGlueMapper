# KingdomComeGlueMapper

**Bring Kingdom Come: Deliverance terrain, vegetation and static buildings into the Kingdom Come: Deliverance II engine.**

[![Validate tooling](https://github.com/SamG-Coder/KingdomComeGlueMapper/actions/workflows/validate.yml/badge.svg)](https://github.com/SamG-Coder/KingdomComeGlueMapper/actions/workflows/validate.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/Code-MIT-green)](LICENSE)

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/531e91e2-9eff-46da-bc7d-12e50ab1eaad" />

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/e601470d-dca1-4fbc-9d8f-26a2f3db0ac3" />

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/4f94a6ad-85fe-4117-98ea-b96bccdf8238" />

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/22ed5177-6a85-4365-858f-d8746db6bff6" />


*User capture of the v20 full-map building build near Rattay. Buildings are rendering, but the visible stray water plane and missing river sections in this area remain known issues. The debug FPS is from this incomplete world, not a full-game benchmark.*

KingdomComeGlueMapper is an experimental Python converter that reads **compiled files from your installed games** and builds a separate test level for the official KCD2 Modding Tools. Raw editor source is not required by this workflow.

**Windows setup executable:** the [experimental setup release](https://github.com/SamG-Coder/KingdomComeGlueMapper/releases)
finds both Steam games, builds the converted world from their installed archives,
and installs the retail mod with progress bars and a saved live log. No Python
or modding-tools installation is required to run the EXE. See the
[Windows setup guide](docs/windows-setup.md) for disk requirements and limitations.

**New in v0.2.0-alpha.2:** [experimental region travel](docs/region-travel.md)
adds **Travel to KDC1** to the Trosky coachman's dialogue, using your existing
KCD2 character. The setup now offers **Build region travel** after the base world
is installed; the resulting overlay is installed separately. The current build
includes road-derived horse arrival positions and return-coachman registration.
Horse calling, the corrected return Talk prompt and repeat trips still need
verification. This does not complete the KCD1 campaign.

Work toward a retail **Play KDC1** mod has started. The [retail setup](docs/retail-setup.md)
now audits the opening quest from installed KCD1 archives and builds a package
whose startup script has been verified in an ordinary KCD2 retail launch.
The optional **Play KDC1** entry can now route native New Game to the converted
world in retail. Enable the experimental route with `update-runtime --start-probe`;
quests and campaign save/load validation remain unfinished. The retail setup
reads game archives directly, without the modding-tools reference database.

`setup_campaign.py build-campaign` can now bundle an existing converted world,
its imported asset dependencies and the retail opening quest sources under the
stable map name `kcd1_rataje`. It includes a native spawn point read from KCD1's
`spawnStart` beside Henry's home. Quest translation and the remaining gameplay
systems are unfinished; see the
[setup instructions](docs/retail-setup.md).

Terrain, original close-up materials, individual vegetation, merged grass / ground cover, and water are working in game. The development launcher places the player near the starting town of **Skalitz** with no-collision flight enabled.

Registered NPC packages now have a [male and female character upgrader](docs/character-upgrader.md)
for source body geometry, native rig conversion and an optional fixed initial
outfit. It stages changes with preservation checks and reversible installation;
the parent idle test now shows the restored female arms and corrected neck.
Broader animation and independent equipment validation remain unfinished.
The default retains the original body proportions and packs native skin weights.

## What works today

| Component | Status |
|---|---|
| Compiled terrain conversion | KCD1 terrain version 28 → KCD2 version 29 |
| World height and layout | 5,461 nodes and 17,139,968 samples checked |
| Sector continuity | 519,488 shared-boundary comparisons; largest mismatch under 5 cm |
| Original terrain materials | All 26 material paths resolve in the runtime |
| Close-up textures | Visually confirmed soil and grass detail |
| HD texture inputs | 78 texture families, with installed HD overrides and streamed mip files |
| Test launcher | Terrain-height spawn, Skalitz default, no-collision flight, intro-skip settings |
| Texture transitions | Optional softer-blending experiment; original transition fidelity not established |
| Individual vegetation | v24: 718,931 foliage placements, plus instanced rocks and clutter; v11/v13 appearance confirmed, expanded coverage and LOD transitions need broader checks |
| Merged grass / ground cover | 25,405,423 samples / 83,431 cells / 169 groups; v13 confirmed working in game |
| Water volumes | 282 original volumes; v17 appearance and reflections confirmed with native KCD2 water material and daytime cubemap |
| Static buildings | v20: 41,682 placements / 1,817 meshes across the map; buildings visually confirmed near Skalitz and Rattay, broader checks pending |
| Static props and furnishings | v26 interiors confirmed furnished; 120,776 additional brush placements. v28 contains 10,250 visual item entities, including 31 item material fixes; broader validation pending |
| Complete individual-instance coverage | v24 reads all 1,483,225 source instances, including instanced stones and clutter; adds 177,466 foliage placements over v13. Visual LOD alignment remains under investigation |
| Original opening position | `--kcd1-start` uses the opening quest's player CutsceneSpot, TagPoint231, including source height and facing |
| Road objects and decals | v34: 14,198 roads and 15,698 decals in shared/initial layers; broad visual validation pending |
| NPCs | Imported parent characters in the development population probe; body, clothing and animation compatibility still under validation |
| Quests | Source audit, campaign packaging and opening-world integration in progress; full quest execution and save/load are unfinished |

**Latest coverage audit:** see [the world import checklist](docs/world-import-checklist.md).
The v34 development build adds 14,198 shared/initial-state road records, 15,698
decals and 26 audited level-local landscape meshes. These additions are still
under visual validation. NPC and quest work is described above. The checklist
supersedes older milestone coverage statements above and distinguishes actual
omissions from intentionally excluded states, helpers and distant proxies.

This is a **terrain-porting prototype**, not a complete game port. Visible gaps, material transitions and missing world objects are still under investigation. Highest-resolution mip residency and walking collision have not been comprehensively validated.

## Requirements

- Windows and **Python 3.11 or newer**. The tools use the Python standard library.
- Installed copies of both games.
- Official **KCD2 Modding Tools**, with workspace setup completed and access to the KCD2 game assets.
- Space for separate generated level packages and material assets. The current material bundle is approximately 234 MB; loose runtime assets take an additional copy.

The default Steam library is `D:\SteamLibrary\steamapps\common`, containing:

```text
KingdomComeDeliverance/
KingdomComeDeliverance2/
KCD2Mod/
```

The tools target the observed `rataje` → `trosecko` compiled formats and 4096 m terrain layout. They are not a general CryEngine converter, and other game builds may need adjustments.

## Quick start

Clone this repository, open PowerShell in its directory, and build the visually confirmed material configuration:

```powershell
git clone https://github.com/SamG-Coder/KingdomComeGlueMapper.git
cd KingdomComeGlueMapper

python tools/upgrade_map.py --original-materials --output 'D:\SteamLibrary\steamapps\common\KCD2Mod\Data\Levels\kcd1_terrain_v6'
python tools/verify_compiled_conversion.py --level kcd1_terrain_v6
python tools/launch_probe.py --level kcd1_terrain_v6
```

Close any running Kingdom Come instance before launching. The builder refuses to overwrite an existing level or asset namespace; use a new output name for another experiment.

The launcher waits for the level and player, queries terrain elevation, and places the player above the ground at **X 734.89313, Y 3421.4497**. These coordinates come from an original scene marker beside Henry's opening conversation with his mother in Skalitz; they are not a verified exact bed-spawn position.

No-collision flight is on by default. Use `--walk` for a traversal test, or `--attach --level <current-level>` to reapply spawn and flight settings to an already running test. `--x`, `--y` and `--clearance` override the spawn position.

Intro-skip settings are passed to the development runtime. They are recognized by the engine, but skipping every startup video has not been visually verified.

### A different Steam library

Supply the library and workspace paths explicitly:

```powershell
python tools/upgrade_map.py --library 'E:\SteamLibrary\steamapps\common' --original-materials --output 'E:\SteamLibrary\steamapps\common\KCD2Mod\Data\Levels\kcd1_terrain_v6'
python tools/verify_compiled_conversion.py --library 'E:\SteamLibrary\steamapps\common' --level kcd1_terrain_v6
python tools/launch_probe.py --tools 'E:\SteamLibrary\steamapps\common\KCD2Mod' --level kcd1_terrain_v6
```

### Softer texture transitions

For a separate comparison build:

```powershell
python tools/upgrade_map.py --original-materials --terrain-blend-factor 1 --output 'D:\SteamLibrary\steamapps\common\KCD2Mod\Data\Levels\kcd1_terrain_v7'
python tools/launch_probe.py --level kcd1_terrain_v7
```

This adjusts material transition shading while preserving terrain geometry, surface assignments, texture inputs and UV scales. It does **not** reconstruct missing original blend masks. The launcher defaults to v6, the screenshot-confirmed material checkpoint.

## How the conversion works

### Vegetation conversion (experimental)

The working **v11** build restores **541,465 individual vegetation instances**
across **226 meshes**, including original trees and shrubs. The user confirmed
visible nearby trees and distant forest in the [v11 screenshot](docs/images/kcd1-vegetation-in-kcd2.png). The exported
HLOD stream contains 3,980 spatial vegetation sectors, and all 226 models loaded
without isolated-asset load errors in the captured runtime log.

The converter reads original position, scale and rotation, rewrites embedded
absolute and relative material references in CGF files, preserves available LODs,
and packages streamed texture families under an isolated namespace. Normal-map
filename suffixes are preserved because the engine uses them to identify texture
semantics. Terrain geometry and surface samples are unchanged from v6.

An important format difference: the initial small probe put instances in the
terrain object tree, but no vegetation was visible. The working build puts them
in KCD2's **`terrain/hlods.dat` and `terrain/hlods.xml`** structure instead. The
record layout was checked against 4,669,105 native vegetation records and 171,333
brush records; generated XML offsets and instance counts are also validated.

After building the v6 terrain/material baseline:

```powershell
python tools/build_vegetation_probe.py --level kcd1_vegetation_v11 --all
python tools/launch_probe.py --level kcd1_vegetation_v11 --x 748 --y 3427
```

`--all` includes every decoded individual instance whose mesh is under the
original vegetation path. Without it, the tool builds a small two-species patch.
This is not complete individual vegetation coverage: the reader decodes leading
vegetation records in each source block and stops at unsupported record types.
Distant proxy atlases, advanced wind behaviour and exhaustive collision/LOD
validation remain unfinished.

#### Merged grass and ground cover (v13 confirmed working)

```powershell
python tools/build_vegetation_probe.py --level kcd1_groundcover_v13 --all --merged
python tools/launch_probe.py --level kcd1_groundcover_v13 --x 748 --y 3427
```

`--merged` converts all **25,405,423 compact merged samples**, covering **83,431
cells and 169 vegetation groups**, alongside the individual vegetation selected
by `--all`. It preserves the original 12-byte position/scale/rotation records,
combines the two legacy sector parts, emits KCD2-shaped merged render nodes in
the terrain octree, and writes matching geometry identifiers into the descriptors
and sector streams. Models, materials and streamed textures use the same isolated
asset packaging as the individual vegetation build.

The user confirmed **v13 merged grass and ground cover working in game**. This
remains a prototype: comprehensive checks of every group, visibility bounds, LODs
and wind behaviour are still outstanding. The [v17 water screenshot](docs/images/kcd1-water-in-kcd2.png) includes merged ground cover.

The local v13 level completed loading and confirmed terrain-based spawn with
no-collision flight. Every packaged sector was checked against its octree group
descriptors; all 83,431 cell counts and geometry identifiers matched. The runtime
also reports missing item database definitions for three namespaced plant models;
harvesting/respawn gameplay is not converted.

Assets are installed under `KCD2Mod/Data/glueveg/<level>/`. The vegetation level
also depends on the base level's terrain-material namespace; keep those assets
installed. Source game assets and generated outputs are not distributed here.
The default launcher remains on the terrain-only v6 checkpoint; select v11
explicitly as above.

Asset-free binary checks: `python -m unittest discover -s tests -v`.

### Water conversion (v17 confirmed working)

The user confirmed water appearance and reflections working in the v17 build,
shown in the [v17 water screenshot](docs/images/kcd1-water-in-kcd2.png). This local confirmation does not establish full river coverage. Build on the existing v13 ground-cover level:

```powershell
python tools/build_water_probe.py --base-level kcd1_groundcover_v13 --level kcd1_water_v17 --native-water-materials
python tools/launch_probe.py --level kcd1_water_v17 --x 730 --y 3270 --clearance 5
```

This converts **282 original water volumes**: 197 area polygons and 85 river
segments. Original surface and physics-contour coordinates, volume IDs, fog
settings, depth and flow speed are preserved. The 57 source material-less volumes
remain material-less. The existing terrain and vegetation are retained.

The confirmed configuration uses `--native-water-materials` to bind the installed
KCD2 river material. The original global environment probe is restored with an
explicit installed KCD2 **07:00 cubemap**. This avoids the missing texture fallback
from an empty dynamic probe path in a newly generated level. The cubemap is a
**fixed daytime fallback**; the original day/night probe sequence and local baked
probes have not been converted.

Omitting `--native-water-materials` retains the experimental conversion of seven
original water materials, packaging textures under `KCD2Mod/Data/gluewater/<level>/`.
That path translates shader features into both named features and numeric masks
using the installed target definitions. Named features alone did not activate
reflections in this runtime. The original-material path is not the visually
accepted v17 configuration.

Validation covers the complete source and native object streams, including road
record strides needed to locate water safely. All 282 surface/physics-contour
payloads were compared byte for byte, and the two new auxiliary defaults match
all 115 installed native water volumes. The runtime confirmed level load, fly
mode and an active probe with the explicit cubemap bound. The asset-free suite
includes water and static-brush record checks. Swimming, full flow behaviour and exhaustive shoreline checks
remain unverified. Keep the base terrain and vegetation asset namespaces installed.

### Static buildings (experimental)

The first building probe selects intact and shared static structures within
400 metres of the Skalitz starting area. It excludes the destroyed town layers
and adds buildings, structural meshes and fences to the confirmed v17 water level:

```powershell
python tools/build_buildings_probe.py --base-level kcd1_water_v17 --level kcd1_buildings_v19
python tools/launch_probe.py --level kcd1_buildings_v19 --clearance 5
```

The selected source contains **4,555 placements across 586 meshes**. Conversion
preserves each original transform, maps the 100-byte source brush record to the
104-byte target layout, and appends spatial clusters to the existing HLOD stream.
Models, available LODs, materials and streamed texture families are packaged under
`KCD2Mod/Data/gluebuild/<level>/`, including installed HD and patch overrides.
Existing terrain, vegetation and water object payloads are retained unchanged.

The initial v18 runtime loaded buildings but visual testing exposed incorrect
second-UV texture bindings and burned debris mixed into the intact town. The v19
converter maps Illum second-UV colour textures from `[1] Diffuse` to the target
`Opacity` slot and excludes ambiguous `pc` layers. Named shader features take
precedence over serialized numeric masks, whose global bits are not shader-local
`.ext` masks. The original separate tiled blend mask cannot share the target
second-UV slot, so exact moss/dirt blend fidelity remains unverified.

Visual alignment, material appearance, streaming and collision validation remain
pending. Original CGF geometry and physics
chunks are retained, with a neutral target collision class. Dynamic doors, NPCs,
quest-driven layer changes and full-world building coverage are not included.
Keep the inherited terrain and vegetation asset namespaces installed. `--radius`
changes the selection radius from 1 to 1,000 metres; choose a new `--level` name
when rebuilding because existing outputs are never overwritten.

#### Full-map structures

```powershell
python tools/build_buildings_probe.py --all --level kcd1_buildings_v20
python tools/launch_probe.py --level kcd1_buildings_v20 --clearance 5
```

`--all` removes the Skalitz distance restriction and includes shared layer-zero
structures plus explicitly named `_state0` and `_state0_prefabs` layers across
the original world. The installed source selects **41,682 placements across
1,817 meshes**. This is geographical expansion of static structures, not a
conversion of every quest state: later states, ambiguous platform layers and
dynamic entities remain excluded. The generated report lists excluded layers
and their placement counts. Existing water and vegetation are retained.

Large builds stage assets on disk to bound RAM use. Keep enough free space for
both temporary and installed assets; the temporary staging directory is removed
when the build finishes. Full-world visual, collision and streaming coverage
still requires in-game validation.

The local v20 build installed 45,927 files (approximately 9.1 GB), with no missing
material texture references, and completed runtime loading with flight enabled.
Runtime caveats include zero mean-face-area reports for a quarry tent mesh and
a Johanka stairs proxy, empty bounds on tent/rope pieces, a missing render mesh
on an ash-pile LOD, and a rejected collision proxy for the large Uzhitz
church interior (6,427 triangles exceed the target's 5,000-triangle limit).
These warnings do not establish visual failure, but collision is not fully converted.

### Known water issues near Rattay

The user reported a stray flat water surface beside the castle/bridges and missing
river sections in the v20 build. The screenshot above records the stray plane.
The recorded inspection position was approximately **X 2798, Y 504, Z 52**.
A nearby original water volume references `materials/terrain/river/river_sazava_02`,
but this proximity does not prove it causes the visible plane. The source volume
selection, target polygon rendering and missing river geometry require further
investigation. Neither issue is fixed by this buildings checkpoint.

#### Stream geometry and castle-plane test (v21 / v22)

The original world also contains separately placed stream surfaces, banks and
waterfalls. These were outside the first water-volume and building selections.
The incremental v21 build adds **8,802 placements across 117 models**, including
their original transforms, material overrides and LODs, without rebuilding the
existing building assets:

```powershell
python tools/build_buildings_probe.py --all --streams-only --base-level kcd1_buildings_v20 --level kcd1_streams_v21
```

The user confirmed the stray castle plane disappears when water volumes are
temporarily disabled. A separate v22 test removes only the surface material of
the suspect sloped volume `0x44bfc6118f6b83a9`, retaining its geometry, fog and
physics-contour bytes and leaving all other volumes unchanged:

```powershell
python tools/build_water_surface_probe.py --base-level kcd1_streams_v21 --level kcd1_water_v22 --suppress-surface 0x44bfc6118f6b83a9
python tools/launch_probe.py --level kcd1_water_v22 --x 2764.04 --y 681.137 --clearance 5
```

This is a **targeted rendering workaround**, not a reconstruction of the original
sloped-water behavior. The combined test loaded successfully, but visual river
coverage and physics behavior still require confirmation. The 57 original
material-less river segments remain unchanged; assigning a surface to them
without evidence could add further unwanted water. Keep the v20 and v21 asset
namespaces installed. Existing level outputs are never overwritten.

### Terrain and materials pipeline

1. Read compiled terrain and level metadata from the installed KCD1 `rataje` archives.
2. Validate the observed binary layout and compare it with installed KCD2 data.
3. Convert the node headers, height/surface packing and geometry-error layout.
4. Rebuild the minimal runtime level and mission documents, leaving outdoor objects explicitly empty.
5. Optionally package the original terrain materials and all referenced DDS families, overlaying installed HD files on their base assets.
6. Verify sample preservation, sector boundaries and material dependencies before testing in the target runtime.

A key discovery was that the installed KCD1 height samples already use **fixed 5 cm increments**, despite a legacy variable-range value remaining in their headers. Using that legacy range produced steps up to 46.2 m between sectors. Fixed-step decoding restored continuous terrain and matched live target-engine height queries.

Terrain material feature masks are translated through named shader features instead of copying numeric masks across engine versions. Texture dependencies include numbered streaming mips and split alpha/gloss files; copying the base DDS alone is insufficient.

## Generated files and isolation

The converter writes a new level under `KCD2Mod/Data/Levels/<name>`, plus an `upgrade-report.json`. Material builds also create:

```text
KCD2Mod/Data/gluemapper_<name>.pak
KCD2Mod/Data/gluemapper/<name>/
```

The loose namespace is required by the tested development runtime: placing an arbitrary archive in `Data` did not automatically make its materials available. Every asset reference is namespaced to avoid replacing official materials or textures.

Retail archives and the modding workspace's retail symlinks are not modified. To remove a test, close the game and remove only its generated level directory, matching `gluemapper_<name>.pak`, and matching `gluemapper/<name>` directory. Keep any output still used by another test.

## Tools and validation

The current visible-item pass preserves original world transforms, resolves pickable-item
database models, and composes parent-local item placements. Items use static visual
entities: inventory, pickup, container interactions and quest logic are not imported.
Shared mission entities and explicitly identified initial-state layers are included;
later states and unsupported models are recorded in the local reports.
Level-local designer geometry is excluded: the v27 experiment exposed editor
blockers and giant helper planes, so v28 restores the v26 geometry set.

To build from the water/stream checkpoint and the original v13 vegetation report:

```powershell
python tools/build_buildings_probe.py --base-level kcd1_water_v22 --level my_props --all --props-only
python tools/complete_instances.py --base-level my_props --level my_world --original-report "D:\SteamLibrary\steamapps\common\KCD2Mod\Data\Levels\kcd1_groundcover_v13\vegetation-report.json"
python tools/launch_probe.py --level my_world --kcd1-start
```

Use fresh level names. `complete_instances.py` requires a base with exactly the
individual-instance set recorded in that report; it preserves existing buildings,
water, merged sectors and resource indices. `build_entity_probe.py` can append missing
visual entities to an existing checkpoint without adding duplicates by source ID.

In the development console, switch to walking with
`#player.player:SetFlyMode(FlyMode_Off)` and back to flight with
`#player.player:SetFlyMode(FlyMode_OnNoCollisions)`.

The installed KCD2 shader include has a compilation failure in an instanced variant:
`Get_BindlessBoneOffset_Prev` references an unavailable `CD_CustomData` constant.
`python tools/fix_instanced_shader.py` generates a small loose include override from
the user's installed shader source, selecting the instance buffer for that variant.
It also corrects `ShadowMotionBias`, which references an unavailable
`CD_CustomData2` in instanced shadow variants; those variants use a neutral
per-object multiplier of one because the instance buffer has no such field.
It does not modify archives or distribute engine source. Both reported compilation
errors are resolved in v28: the inspected log contains 1,984 distinct compilations
and zero compile failures. The user confirmed the test completed. Wider shadow
appearance and LOD alignment validation remain necessary.
Restart after installing the overrides. They can be removed by deleting only
the generated loose `ModificatorVT.cfi` and `CommonShadowGenPass.cfi` files in
`KCD2Mod/Engine/Shaders/HWScripts/CryFX/`.

| Tool | Purpose |
|---|---|
| `tools/audit_maps.py` | Read-only archive inventory and bounded header inspection |
| `tools/compiled_terrain.py` | Terrain parser and elevation converter |
| `tools/upgrade_map.py` | Build an isolated compiled test level |
| `tools/terrain_materials.py` | Resolve, namespace, package and verify texture dependencies |
| `tools/verify_compiled_conversion.py` | Check every height/surface sample and shared sector boundaries |
| `tools/build_vegetation_probe.py` | Build original vegetation instances and KCD2 HLOD metadata |
| `tools/vegetation.py` | Bounded vegetation/HLOD readers and instance conversion |
| `tools/launch_probe.py` | Launch the development runtime and apply spawn/flight settings |
| `tools/build_water_probe.py` | Build original water volumes with the confirmed native-material option |
| `tools/water_volumes.py` | Bounded object-stream reader and water record conversion |
| `tools/build_buildings_probe.py` | Build intact Skalitz static structures on the water checkpoint |
| `tools/building_brushes.py` | Static brush record conversion and resource-table checks |
| `tools/static_assets.py` | Package referenced CGF, material and texture assets in isolation |
| `tools/entity_visuals.py` | Resolve initial-world item models and parent transforms |
| `tools/build_entity_probe.py` | Append visible furnishings and placed items |
| `tools/complete_instances.py` | Complete mixed-stream foliage and instanced prop coverage |
| `tools/fix_instanced_shader.py` | Generate a local correction for the installed instanced shader variant |
| `tools/stage_map_probe.py` | Historical unconverted-copy diagnostic; not needed for normal use |

Local checks require your installed game files. GitHub Actions checks Python compilation, CLI entry points and asset-free vegetation binary tests on Windows; it does **not** run the games or establish runtime compatibility.

See [development notes](docs/development-notes.md) for the experiment history and observed format differences. Local logs, reports, extracted engine references and generated assets are intentionally excluded from this repository.

## Roadmap

**Current milestone: vegetation.** Individual vegetation is now rendering in KCD2, with a world-wide v11 build and a visual confirmation near Skalitz. Merged grass and ground cover are also confirmed working in the v13 build. Remaining work includes broader merged-sector checks, proxy/LOD behaviour, wind, collision and placement validation.

1. **Read the vegetation data.** Trace species definitions, placed instances and merged vegetation records in the compiled KCD1 packages, and compare their representation with KCD2.
2. **Convert one representative patch.** Resolve a few tree, shrub and ground-cover assets with their materials and texture dependencies. Preserve source positions, rotation and scale wherever the compiled data provides them.
3. **Validate in game.** Check terrain alignment, foliage transparency, lighting, shadows, collision where applicable, and visibility at near and far distances. Test LOD transitions and streaming before increasing the instance count.
4. **Expand across the world.** Extend the converter to the remaining vegetation types and merged vegetation data, measuring performance as coverage grows.

The first rendering checkpoint is confirmed: original vegetation is visible near Skalitz in a reproducible build. Stable distance transitions, complete coverage and collision still require further validation. A resource-table scan or successful asset extraction alone does not complete those checks.

**Water milestone: v17 confirmed working.** Original water geometry now renders with native KCD2 water materials and a valid daytime reflection fallback. Remaining water work includes the day/night probe sequence, local probes, swimming and broader shoreline/flow validation. The v20 checkpoint expands static buildings across the map. Next work includes the stray water plane and missing river sections near Rattay, remaining placed objects, terrain gaps and material transitions, and road geometry. Walking/collision and world-streaming validation precede NPCs, navigation and quests.

## License and game content

The original Python tooling is released under the [MIT license](LICENSE). Game content, trademarks and the in-game screenshot belong to their respective rights holders and are not covered by that code license.

No game archives, extracted textures, meshes, engine source or generated level packages are distributed here. The converter reads assets from your own installations. This is an independent experimental project, not an official Warhorse Studios or publisher release.
