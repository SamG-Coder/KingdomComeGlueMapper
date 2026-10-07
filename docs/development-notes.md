# KingdomComeGlueMapper

First objective: load and explore the Kingdom Come 1 world inside the Kingdom Come 2 runtime, using the installed compiled game packages. Preserve the original layout. Buildings, vegetation, original materials, NPCs and quests require further conversion.

## Status â€” 2026-10-07

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
