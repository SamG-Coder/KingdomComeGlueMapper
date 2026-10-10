# Retail world streaming audit — 10 October 2026

Inspected the installed retail Trosky level at `Data/Levels/trosecko` and
`outputs/CurrentBuild/Mods/gluemappertravel/Data/Levels/kcd1_travel` directly.
Machine-readable comparison: `outputs/BuildLogs/native-streaming-comparison.json`.
Live log snapshot: `outputs/BuildLogs/render-diagnostic-live.log`.

## Native package structure

- `level.pak`: compiled HLOD tree/data, entity layers, world records, and per-layer
  physics. Trosky has 392 `physics/layer*` files and 1,395 layer XML files.
- `hlod.pak`: distant scenery proxy geometry/materials (`hlods/main/...`).
- `hlod_vegetation.pak`: distant vegetation proxy geometry/materials.
- `terrain.pak`: terrain resources and spatial merged-mesh grass sectors.
- `cestool.pak`: exported road/bank geometry.
- `recast.pak`: navigation groups/tiles.
- `svo-part*.pak` and `IPL_svo.pak`: spatial lighting data, separate from scenery
  instance streaming.

PAK boundaries provide storage/mounting. The compiled spatial structures and
their resource references determine which representations are used at distance;
placing buildings in a separate PAK alone does not implement that behavior.

## Observed difference

| Property | Native Trosky | Imported travel map |
| --- | ---: | ---: |
| HLOD nodes | 5,087 | 12,885 |
| Maximum HLOD nesting depth (root = 0) | 6 | 2 |
| Nodes with a nonnegative proxy index | 4,876 | 0 |
| HLOD types | Cluster, Layer, Prefab, Interior, Vegetation | Cluster, Vegetation |
| Merged grass sector `.dat` files | 46,569 | 83,431 |
| Separate scenery/vegetation proxy PAKs | Both | Neither |

The importer already groups building placements into 64 m cells and vegetation
into spatial cells. Its building generator assigns every cluster radius 400 m
and `ProxyIndex=-1`; vegetation similarly has no far proxy. Grass sector files
are present, so absence of all sector files is not an explanation for missing
grass. The number of cells is not itself a runtime performance measurement.

KCD1's source level contains authored `*_uberlod` layers. The current building
import excludes uberlod proxy meshes. A complete upgrade should investigate
converting those source distant representations or generating compatible KCD2
proxies, retaining source conditional-state ownership, instead of substituting
full-resolution geometry for the entire near/far hierarchy.

## Latest log

The diagnostic run loaded Trosky and then KCD1. It contains no explicit
`MAX_REND_SHADER_RESOURCES` / `run out of Shader Resources` error. It does report:

- failure to load the `geometrybeam` shader;
- empty bounding boxes on imported building meshes 62 and 1742;
- invalid physicalization transforms for several tiny instances of prop mesh 119;
- missing optional/level-support XML and material lookups. Several material
  lookup errors also occur during the Trosky phase, so they are not sufficient
  evidence of a KCD1-only regression.

The file-verbosity diagnostic worked, but its scheduled NPC snapshots did not
fire across level loading. Its source now restarts bounded observations through
the native Player initialization/load lifecycle, with a regression test. That
source correction was subsequently rebuilt and installed at 17:15 Sydney time,
with all 50 managed files verified. No live shader allocation count or innkeeper
render-slot snapshot is available from the earlier run.

## Boundary at the initial audit (superseded by the conversion below)

Scenery needs native HLOD grouping, valid bounds, near/far proxies and verified
merged-mesh sector references. NPC schedules, entity activation and conditional
layers are a separate concern; adding NPC-style layers to static geometry is
not a substitute for the scenery export structures.

Next implementation should preserve placements and material/mesh slot identity,
convert the source hierarchy/dependencies, and validate every proxy index,
payload offset, bound and resource reference. Test the resulting streaming in
retail by approaching/receding from buildings and woodland, changing regions,
and checking NPC visibility. Missing HLOD proxies are confirmed, but they do not
by themselves prove the cause of every invisible object or the innkeeper.

## Whole-world resource pressure and startup traces

The current imported HLOD payload contains 167,167 static brushes and 1,483,225
individual vegetation placements. These are serialized placement counts, not a
measured draw-call count. A fast GPU cannot establish that the engine's shared
material/shader resource pool is within its limit. The earlier retail fatal log
explicitly reached 32,768 shader resources.

The world package declares 14,576 shader-bearing material entries; the travel
package declares 5,552 after the population sharing pass. The combined 20,128
declarations exclude native KCD2 and runtime instances. Declarations must not be
presented as live allocation counts or treated as separate per-mod budgets.

The source contains 4,041 vegetation and 114 brush uberlod groups. Native KCD2's
`ProxyIndex` refers to a record in the **parent node's HLOD payload**, not directly
to the global terrain mesh table. Verified examples include Trosky's castle
(parent record 3 resolves to its castle HLOD mesh), and Main_0 (parent record 6
resolves to its exported cluster proxy). Restoring these relationships requires
converted proxy resources and correct membership; assigning an arbitrary
positive index would select unrelated geometry.

All 4,155 source far-object definitions have now been matched uniquely to their
original brush records using layer, position and bounds (2 mm tolerance for the
XML float serialization). The mapping is saved in
`outputs/BuildLogs/source-world-proxy-bindings.json`. No definitions are missing
from that lookup; geometry/material conversion and target near-group ownership
are still required before these proxies can be mounted safely.

The latest log contains 400 `human_host` messages: 200 begin/success pairs, all
`count=1`, before KCD1 enters RUNNING. The native `AI/npc/basic/switch/switch.xml`
calls `crime_getMrkev` in OnInit. This trace observes a graph lookup, not a
render-load measurement. It exhausted the old global diagnostic limit and hid
later reaction events. The source now uses a separate startup trace allowance,
the native IsLoadedGate, and five bounded actor censuses after player loading.
Diagnostics also read the world HLOD/geometry/grass streaming CVars. They do not
change loading, rendering, population, or memory budgets. Whole-world streaming
and visible rendering remain unverified until the corrected export is tested.

## Scenery conversion implemented after the latest capture

The next capture (`outputs/BuildLogs/world-loading-latest.log`) separates the
Trosky phase from the `map kcd1_travel` transition. There are no imported
`gluebuild` / `glueveg` geometry load entries during the Trosky phase; 4,637
appear after the KCD1 level opens. Root mod archives and database catalogs are
mounted globally, which is different from loading every scene's geometry.
This does not measure the global catalogs' startup cost. Shader resource counts
in that capture are 18,377 in Trosky and 23,279 in KCD1, below the earlier 32,768
exhaustion. The far actor census finds 927 hidden actors, 919 without render
slots. Neither a global NPC registration message nor an archive-open message
establishes that an offscreen body or distant building is being drawn.

`scenery_streaming.py` now converts the source's far-object definitions into
parent-local KCD2 `ProxyIndex` references. Vegetation uses its authored 64 m cell
identity; building groups use the source ownership polygons. Detailed brush
records outside those polygons retain spatial cells with bounds computed from
their actual geometry. A spatial parent hierarchy replaces the flat list of
large, overlapping clusters. Source switching spheres are expanded only when
needed to contain the retained near geometry. Source state1/state12 building
substitutes are not enabled over the intact state0 scenery.

Both exported levels contain 7,190 HLOD nodes and 4,141 near/far links, compared
with 12,885 nodes and zero links before this change. All 167,167 near brushes
and 1,483,225 individual vegetation placements are retained byte-for-byte in
the regrouped payloads. Terrain heightfields, surface assignments, merged grass
sector files, actors, layer registrations, quests, shops and navigation are
unchanged. Parent indices, block bounds, unique ownership and record retention
are checked during conversion. The exported proxy render flags come from the
installed KCD2's native proxy records; obsolete KCD1 uberlod identifiers are
cleared from the converted far records.

New proxy geometry, materials and textures are in each level's `hlod.pak`, under
its own `levels/<level>/hlods/kcd1` namespace. This follows the native level
archive loading path rather than adding them to the globally mounted root
asset shards. The dependency validator resolves 4,145 proxy mesh assets,
82 materials and 177 texture references in each archive, and checks its CRCs.
Copying the converted world to the travel level retargets these material and
resource paths, preserving the mesh geometry. No KCD2 world is modified.

The desktop setup plan also still contained a rejected historical
`repair_tree_materials` stage. It is now removed. The current package's 34
affected materials are restored by matching source structure and texture
identity; material slots and converted texture paths are preserved. This
reverses the explicit shadow-slot suppression and restores original sibling
shadow flags. It does not assert that every screenshot defect came from that
patch.

The world bootstrap's temporary verbose logging and the six native AI trace
overrides are removed from the normal package. The player's travel/horse hooks
are retained. Diagnostics remain an explicit development option.

The conversion runs in `current_build` and campaign setup, so rebuilding will
retain it. Validation: 354 unit tests passed; 20 native database tables passed
the installed mod tools' validator. Archive comparison against the installed
baseline found only the intended scenery, material and diagnostic changes.
Reports are `outputs/BuildLogs/scenery-upgrade.json` and
`outputs/BuildLogs/scenery-streaming-validation.json`.

Installed at 17:55 Sydney time on 10 October: 14 changed managed files, all
54 managed files verified, no obsolete-file removals. Each level-local HLOD
archive is 180.9 MiB. The same hierarchy/dependency/CRC validation was rerun
against the installed retail copies; see
`outputs/BuildLogs/scenery-installed-validation.json`. The immutable older
TravelBase cache's diagnostic variant is recognized during rebuilds, without
removing the player's gameplay hooks.

Retail visibility, near/far switching and travel startup performance still
require a fresh in-game pass. These structural checks are not an FPS benchmark
or a claim that the reported invisible innkeeper has been visually verified.

## Unresolved regression at branch snapshot

The user reports that missing scenery and the invisible innkeeper persist with
the `services-only` build profile (coachman, innkeeper and Theresa). Reducing the
population did not resolve the visual issue. Material compatibility remains
under investigation; no material fix has been verified in retail.

Use the last working commit on `main` as the regression comparison baseline,
not the generated World cache. Local `main` was `4bff077` when this snapshot was
prepared. The current branch preserves the population, streaming, diagnostics
and build-profile work for comparison; passing unit tests does not establish
that rendering works. The source snapshot passed all 358 unit tests.
