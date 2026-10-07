# World import checklist

Audit date: 2026-10-08. Current road fix build: **kcd1_world_v43**.

Runtime check: v34 completed loading in 12.44 seconds and confirmed the
terrain-based spawn. No shader compile failures were found in the inspected
startup log. v42 subsequently loaded in 12.14 seconds, with no shader compile
failures found in its inspected log. Missing AI/gameplay data errors remain.
v43 completed its terrain-based spawn; the user confirmed the road visibility
correction works. All 45 asset-free tests pass;
this does not establish full-map visual correctness.

The audit read all **6,990 editor layer files** (351,948 object entries, zero
parse errors), the installed map's **1,969 compiled layer definitions**, and
**1,832 mission/layer entity XML files** containing 85,874 unique entity IDs.
The scope is `rataje`, not other maps or DLC levels in the editor archive.

Local detailed evidence:

- `reports/world-layer-audit-v34.json`: every editor layer, compiled layer,
  entity class, package category, and unmatched brush path.
- `reports/world-layer-checklist.md`: searchable inventory of all 6,990 editor
  layers and every encountered editor type / compiled entity class.
- `reports/designer-material-classification.json`: material inventory of the
  2,408 originally excluded shared/initial level-local brushes.

The editor archive and installed retail map may be different revisions.
Editor objects, expanded prefab children, road segments and merged vegetation
have different counts. A file/record being present does not prove its appearance,
collision, interaction, shader behavior or streaming is correct.

## Implemented data coverage

- [x] Terrain heightfield conversion: 5,461 nodes. v43 corrects 4,096 sector
  origins to KCD1's 5 cm grid, preventing terrain from covering road surfaces.
  All 17,139,968 height/surface samples pass numerical verification; sample arrays
  and palettes remain unchanged. Source edge differences of up to one height
  step remain; this is not a claim of zero seams.
- [x] Individual instances: all 1,483,225 source instance records are present in
  the target HLOD stream. Appearance, wind, collision and every model's LODs are
  not exhaustively validated.
- [x] Merged vegetation: 83,431 converted cells. The original 142,830 render-node
  count is not a missing-cell count: the conversion combines legacy sector parts.
- [x] Static brush coverage measured: 167,167 target brush placements match source
  bounds/transforms. This matching does not independently verify material/mesh
  identity or appearance.
- [x] Furnishing/item visual resolver: all 10,250 entities currently resolved by
  the original importer are present. v35/v37 add 1,081 visuals: 949 doors,
  113 ladders, 11 grindstones and 8 shooting targets. CDF props use native
  AnimChar bind-pose geometry; this does not implement interaction or animation.
- [x] Compiled water records: all 282 are present. Native fallback water materials
  and earlier suppressed surfaces mean original water behavior is still partial.
- [x] Shared/explicit initial-state road data: **14,198 records** added in v33 and
  retained in v34, including geometry, UV buffers, indices, converted tangent
  bases, source points and physics data.
- [x] Shared/explicit initial-state decals: **15,698 records** added in v33 and
  retained in v34, preserving projection, orientation, size and sort priority.
- [x] Visible level-local landscape subset: **26 brushes** restored in v34:
  20 rock pieces, 5 canal-ground pieces and 1 riverbed piece, selected by an
  explicit audited material allowlist.

## Missing, partial or unverified visual systems

| System | Evidence / remaining work | Status |
|---|---|---|
| Landscape material/terrain joins | User still sees a hard floating grass-sheet edge near Henry's start after the local road/decal pass. Nearby brush transforms match the source. | **Unresolved** |
| World-wide road/decal appearance | v42 corrects tangent-buffer alignment on 6,125 odd-index roads, retaining original mud materials. v43 corrects terrain origins that buried roads at different camera distances. User confirmed the visibility fix works; exhaustive full-map visual coverage remains pending. | **Road alignment and terrain origins fixed; user confirmed** |
| Other level-local designer geometry | 2,382 shared/initial records remain excluded after the 26-piece pass. Most use collision/helper materials; remaining material classes require classification. | Partial; do not enable all blindly |
| Other unmatched brush placements | 1,090 source placements remain unmatched: 1,052 empty mesh paths, 28 default-box paths and 10 other records. Duplicate placement/state ambiguity means these are investigation candidates, not 1,090 proven missing visible assets. | Unclassified |
| Animated doors | 949 previously missing initial door visuals added via CDF/AnimChar in v35/v37; 13 require preserved inline CA_PROX attachments. 76 non-initial variants remain excluded. | Visual data implemented; animation/interaction absent |
| Ladders / grindstones / targets | 113 ladders, 11 grindstones and 8 targets added in v35. Henry's grindstone has a nonempty runtime geometry bounding box. Every individual asset has not been visually verified. | Visual data implemented; interactions absent |
| Other initial item candidates | 164 PickableItem, 65 AnimObject, 25 GeomEntity, 7 AlchemyItem, 2 Stash and 1 LedgerBook candidates lack target visuals. | Needs class/property/asset investigation |
| Item slots | 6,278 initial slots lack a separate target entity, but 5,269 already link to placed items and 967 are intentionally not spawned. Do not count those as missing meshes. Six unlinked local-transform slots and other resolver failures need investigation. | Mostly intentional; remaining cases unresolved |
| Model/parent resolution | Character-enabled v37 resolver leaves 228 unresolved/character models, 72 entities with parents outside its selected set and 6 unsupported unlinked-slot transforms. | Partial |
| Local lighting | 2,237 Light and 2,435 ReflexLight source entities absent. | Missing |
| Environment probes | 1,057 source EnvironmentLight entities; target has one global fallback probe. Original local probes and their time sequence are absent. | Partial |
| Fog and local weather | 352 FogVolume and 2 Rain entities absent. | Missing |
| Particle effects | 6,977 ParticleEffect source entities absent. Includes authored environmental effects; specific effect classification still needed. | Missing |
| Clip volumes / indoor visibility | 1,054 ClipVolume source entities absent; target indoor.dat is an empty 20-byte header versus 464 bytes in source. | Missing |
| Occlusion data | Source occluder.ocm is absent. | Missing; target rebuild/format work needed |
| Distant world proxies | 4,155 source proxy brush records excluded. Their near/far activation graph is absent; rendering them unconditionally caused the overlapping tree/building bugs. Ordinary per-model LOD files remain. | Deliberately excluded until switching implemented |
| LOD areas | 184 source LODArea entities absent. | Missing |
| Procedural objects | 2,188 ProceduralObject entities outside the current visual importer. Editor AreaFiller/VegetationArea outputs may already be baked into imported instances; generator-to-output mapping has not been established. | Unverified / partial |
| Ropes / geometry caches | 12 RopeEntity and 5 GeomCache source entities absent. | Missing |
| Audio | 1,034 ambience areas, 104 random areas, 51 audio-area entities and 301 trigger spots absent. | Missing |

Counts in this table are compiled entity counts, including state variants unless
explicitly labeled initial. They must not be added to editor-layer counts.

## Deliberate scope exclusions and gameplay

- [ ] Layer activation graph / quest-state transitions. 38,085 non-initial brush
  records are excluded. The surface pass also excludes 512 roads and 4,514 decals
  outside shared/explicit initial-state layers. Some custom-named layers may be
  appropriate for the starting world; they require hierarchy/activation analysis.
- [ ] NPCs, animals and AI: 2,054 NPC and 333 NPC_Female entities, character
  animation entities and animal classes are not imported as functioning actors.
- [ ] Navigation meshes, paths, smart objects, ledges and triggers. Source
  `areasmission0.bai`, `ubernav.tmm`, `triggerareas.fubar` and related gameplay
  files are absent. Visual roads are separate from AI navigation.
- [ ] Quests, shops, interactions, inventory, harvest/respawn, cinematics and
  audio logic. Static furniture does not imply those systems work.
- [ ] Full collision/physical-material conversion. Source brush collision
  classes are currently neutralized; excluded designer barriers need a dedicated
  non-visible collision path, not ordinary visible-brush import.

Editor comments and reference pictures are authoring aids, not missing in-game
objects. Prefabs/groups are partly represented by compiled children; importing
their editor instances again could duplicate geometry. Unspecified editor types
remain unclassified in the detailed inventory.

## Work order

1. **Done in data:** world-wide shared/initial roads and decals; validate their
   target shader behavior and representative road/terrain joins.
2. **Started:** classify excluded designer geometry; 26 visible landscape pieces
   restored. Keep helpers and collision-only geometry in separate work.
3. Resolve the remaining Henry bank edge by identifying its actual mesh/subset
   and comparing source/native material and rendering behavior.
4. Restore visible doors, ladders, grindstones and other unresolved visual
   classes, preserving source parent/local transforms.
5. Restore compatible local light/probe, clip-volume and particle systems in
   isolated passes, then review state-layer coverage and distant proxy switching.

Gameplay remains a separate phase from the user's requested visible-world pass.
