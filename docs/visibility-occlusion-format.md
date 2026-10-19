# Visibility and occlusion investigation

19 October 2026. The close-range tree/roof LOD issue is still unverified.
No rendering overrides or package changes were installed during this audit.

## Current installed data

| Level | HLOD nodes | HLOD proxy references | Root occluder.ocm |
| --- | ---: | ---: | ---: |
| Native Trosky (`trosecko`) | 5,087 | 4,876 | 572,728 bytes |
| Converted `kcd1_travel` | 7,226 | 4,102 | Missing |

The converted level already has the native four vegetation tiers (including
the vegetation root). Brush groups are flatter than native Trosky, but neither
this difference nor the missing OCM establishes the cause of late detail.
See `trosky-lod-streaming-audit.md` for the separate HLOD, per-mesh LOD and
streaming decisions, the observed switching formulas, and rejected CGFM test.

Crytek describes OCM as authored static occluding geometry rasterized at
runtime, complementing per-object occlusion. It is not a baked table of visible
objects at every location. Stale occluders can incorrectly hide surfaces.
Reference: https://www.cryengine.com/docs/static/engines/cryengine-5/categories/23756816/pages/25534435

## Observed binary formats

`tools/occlusion_mesh.py` reads both observed versions and emits a standalone
KCD2-format candidate. It is deliberately not an automatic setup stage until
coverage against the converted active world and engine loading are verified.

The 16-byte file header contains four magic bytes, a declared mesh count, an
instance count, and the instance-table offset. Integers after the magic are
little-endian. Geometry blocks begin at byte 16: a vertex count, 12 zero bytes,
then triangle-list vertices as four floats `(x,y,z,1)`. Mesh references are
absolute offsets to those blocks.

| Field | KCD1 rataje | KCD2 Trosky / Kuttenberg |
| --- | --- | --- |
| Magic bytes | `FF FF FF FA` | `FF FF FF FB` |
| Instance size | 64 bytes | 52 bytes |
| Instance layout | Matrix 3x4, bounds extents float3, mesh offset | Mesh offset, matrix 3x4 |

The converter retains every geometry byte, mesh reference and transform,
changing the version and placement layout. It validates extents, references,
triangle alignment, finite values and homogeneous coordinates. Native input
passes through unchanged. Unknown formats fail rather than guessing.

The declared mesh count is not a reliable distinct-block count: Trosky reports
222 while storing 117 blocks; Kuttenberg reports 979 while storing 293. The
parser walks geometry by validated byte extent instead.

The public engine-family loader corroborates the KCD2-style 52-byte layout:
https://github.com/aws/lumberyard/blob/master/dev/Code/CryEngine/Cry3DEngine/CCullThread.cpp
This reference is not evidence that KCD2 accepts every older engine format.

## Validation results

- Native Trosky: 117 blocks, 222 placements, 17,516 placed triangles.
- Native Kuttenberg: 293 blocks, 979 placements, 72,720 placed triangles.
- Source rataje: 1,046 blocks, 2,022 placements, 130,005 placed triangles.
- Converted standalone rataje OCM: 4,546,920 bytes; geometry and transforms
  preserved; SHA-256 `b25a43c587e6bfc6e953e396182f631ce610ec944c842be898c9dfd88be649c4`.
- Three unit tests cover conversion/native-layout equality, repeated mesh
  placements, idempotence, malformed ranges/references, and nonfinite values.

The candidate lives in ignored `outputs/new-game-compare/occluder-candidate.ocm`.
It is not installed. Source-authored occlusion may include geometry or states
which the converted world does not retain. Matching transforms alone is not a
valid filter: only three of 2,022 source OCM instances match retained brush
matrices directly, consistent with different export pivots. Do not discard or
accept the remaining instances solely on that comparison.

## Remaining acceptance

Capture an affected asset on first approach, while stationary, and after
retreat. Record the current mesh LOD (`e_DebugDraw 1`), mesh/HLOD switching
distances (`e_DebugDraw 33` or `34`), and HLOD state
(`wh_e_HLodDebugDraw 1`), separately to keep the display readable. A detailed
request waiting on loading calls for a streaming fix; a late request calls for
LOD selection analysis. Neither should be masked by globally forcing LOD0.

Before enabling OCM generation in setup/upgrades: verify the occluder surfaces
against retained static world geometry, test the output in the engine, and
check doors/opened gates/interiors for false occlusion. This work does not
justify changing the current build recipe or claiming the LOD bug is fixed.

## User-authorized full-world test installation

After the offline audit, the user requested applying the whole-world candidate
as a reversible test and will launch the game themselves. Installed identical
`Data/zz_gluemapper_occlusion_test.pak` overlays into retail and Mod Tools. Each
contains only `levels/kcd1_travel/occluder.ocm`; all 2,022 source placements are
included. Existing level archives were hashed before and after and are unchanged.
The original archives therefore require no backup or full build copy.

Installation and rollback helper (local ignored output):
`outputs/new-game-compare/install_occlusion_test.py`; pass `--rollback` with
the game closed to remove only the verified test overlays. The receipt is
`outputs/new-game-compare/occlusion-test-installation.json`.

This supersedes the offline candidate's not-installed status above. Engine
mount/load and visual coverage are still unverified. No LOD distance changes,
mesh splitting, lighting rebake, or setup recipe migration are included.

## Occlusion test result and geometry-derived HLOD test

The user reported that the visible tree/building problem remains. Retail's
log confirms the occlusion test PAK was mounted; this is not proof of visual
correctness. Removed both test overlays using the verified rollback helper.

`tools/rebuild_geometry_hlod.py` implements the next experiment: reconstruct
spatial containers and bounds from final placed render-node AABBs, ignoring
all inherited KCD1 switching radii. It spatially partitions brush groups and
retains the native four vegetation tiers. Parent bounds enclose child bounds
and include their actual elevation. It retains authored proxy membership and
the proxy geometry; it does not generate new distant meshes, per-mesh LODs,
or occlusion geometry. Unsupported nested authored proxies are rejected rather
than flattened into an incorrect membership.

Installed into retail and Mod Tools after confirming the game was closed:

- 6,951 reconstructed detailed groups, 7,737 total spatial nodes.
- All 1,650,413 detailed records and 4,102 proxy links retained.
- Per-group proxy/detail membership checked, not merely global object totals.
- Only `terrain/hlods.xml` and `terrain/hlods.dat` changed in each level archive;
  every other entry was compared byte-for-byte and archive CRCs verified.
- Eight tests pass across the existing spatial writer and new rebuild tests,
  including deterministic rebuilding, invalid input rejection, geometry-based
  bounds, vegetation depth and proxy membership preservation.

Rollback stores only the original two tables in compressed, content-addressed
ZIPs under `outputs/new-game-compare/geometry-hlod-test`. It does not duplicate
the build. Local installer/rollback helper:
`outputs/new-game-compare/install_geometry_hlod_test.py --rollback`.
The game must be closed. Installation receipt records original/installed
archive hashes and per-group old/new bounds. No setup recipe was promoted.
Runtime loading and visual LOD improvement remain unverified.
