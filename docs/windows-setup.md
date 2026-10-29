# Windows setup

Download `KingdomComeGlueMapper-Setup-0.3.28-win64.exe` from [GitHub Releases](https://github.com/SamG-Coder/KingdomComeGlueMapper/releases/tag/v0.3.28). This is an experimental prerelease for Windows x64. Python is bundled; Microsoft Edge WebView2 is required for the local interface. See the [release notes](releases/v0.3.28.md) for changes, contributor credits, validation and limits.

Choose the executable under **Assets**, not **Source code (zip)**. Download
`SHA256SUMS.txt` from the same release if you want to compare the executable's
SHA-256 with PowerShell's `Get-FileHash -Algorithm SHA256`.

The **v0.3.28** release uses gameplay recipe **61** and appearance policy **2**.
Its runtime source includes changes through `cae0d3e`; the tag also contains the
release documentation. Later source changes require a newer build. See
[release, source and runtime status](status.md) before choosing an upgrade path.
Recipe numbers describe package conversion rules, not campaign completion.

**KCD1 Map requires the official KCD2 Modding Tools from Steam before building, installing an existing package, or resuming installation.** Setup detects the tools and offers Browse, Recheck, and Install through Steam. It uses your installed tools to compile shaders; Warhorse's tools are not bundled. Fishing alone does not require them.

Recipe 3 connects the bounded Skalitz opening, selected actors and routines, initial equipment, conversations, quest combat, and final streaming checks to the normal setup build. The redesigned interface groups locations and prerequisites, with one persistent navigation bar. The parallel conversion changes from PR #28 include bounded worker pools, concurrent package reads and hashing, and a workspace lighting cache.

Compiled shader archives stay under the mod's `ShaderCache` folder. **Launch mod** in the setup Library prepares one reusable linked runtime inside Mods and starts the unchanged retail executable. Retail Engine archives are not replaced. See [mod-contained shader loading](mod-shader-loading.md) for the mechanism and validation limits.

## Choose a mod

| Mod | Package version | Required games | What it installs |
| --- | --- | --- | --- |
| KCD1 Map | 0.2.0-alpha.4 | KCD1, KCD2 1.5.6, and official KCD2 Modding Tools | Converted Rattay region, coach travel, supported interactions and services, plus the bounded Skalitz opening/gameplay recipe. |
| KCD2 Fishing | 0.3.0 | KCD2 1.5.6 | Standalone fishing in Trosky and Kuttenberg; also includes converted-map water when built with KCD1 Map. |
| KCD1:MP | 0.1.0 | Completed KCD1 Map, Steam KCD2 1.5.6, KCD:MP 0.44.0 client/server | Separate experimental scenery export with server settings and launch controls. See the [multiplayer guide](kcd1-mp-setup.md). |

The setup version and mod package versions are separate. KCD1 Map does not convert a complete playable campaign or KCD1 saves. Standalone fishing's native-region runtime validation remains pending.

## Install step by step

1. Install the required games and, for KCD1 Map, the official KCD2 Modding Tools through Steam. Run the setup executable as your normal Windows user in writable folders.
2. In **Library**, select one or both mods, then **Configure installation**.
3. **Locations:** confirm detected Steam paths or browse to each game's main folder. Check that KCD2 Modding Tools are detected. Choose a build folder outside the games, such as `D:\BuildKCDMod`. Select **Build & install**, or **Build packages** to prepare files without installing them.
4. **Lighting:** choose a mode from the table below. Surface baking follows the map conversion and gameplay packaging. It does not run during the early tree/vegetation conversion step.
5. **Opening movie:** leave the optional movie off, or locate your installed RAD Video Tools encoder and enable it. FFmpeg and FFprobe must be on PATH. Setup does not download RAD or open a file picker until you choose to locate it. The optional movie currently replaces the shared New Game intro, including ordinary KCD2 New Game. Later campaign scenes are not included.
6. **Review:** check paths, selected mods and options. Close the games before installation, then start.
7. **Activity:** all planned steps appear up front. The current task reports progress when measurable. Overall stage counts are not time percentages. An ETA only appears where throughput can be measured.
8. Wait for **Selected mods installed**. With Steam running, select **KCD1 Map → Launch mod** in Library to use the compiled mod shaders. Fishing alone can use the ordinary Steam launch.

### Lighting choices and space

| Mode | Scope | Extra build-space allowance |
| --- | --- | --- |
| No Baking | Keeps the normal conversion lighting configuration; skips the additional surface bake. | None |
| Buildings | Structures, static props, ground and roads; forest instances excluded from this pass. | 30 GiB |
| All (Forest) | Buildings coverage plus eligible trees and foliage. | 60 GiB |

All modes include visible buildings and vegetation. Optional surface baking prepares data for indirect illumination; the separate shader stage compiles or reuses matching verified shaders for fresh KCD1 Map builds, including No Baking. Setup does not clear retail shader caches. The surface bake uses two compiled CPU workers for tiles below one million triangles, persistent OptiX for dense tiles, and 64-brick GPU batches with CPU preparation/encoding overlap. GPU failures fall back to CPU with the reason recorded. Baking remains experimental, including approximations for layered materials and foliage.

### Shared build cache

Setup keeps reusable outputs under the selected build folder, separate from dated jobs:

```text
D:\BuildKCDMod\cache\cubemaps\   Converted HDR probes
D:\BuildKCDMod\cache\lighting\   Completed surface-bake shards
D:\BuildKCDMod\cache\shaders\    Compiled native shader packages
D:\BuildKCDMod\<dated-job>\       This build's packages, logs and resume checkpoints
```

Reuse is automatic. Select **Rebuild lighting and shaders for this build** on the Lighting screen to bypass reuse for a new build. This checkbox resets after starting a build and when reopening setup. Buildings and All (Forest) use separate cache entries; changing back can reuse the earlier matching result. No Baking skips surface baking while retaining the other entries. Installing an existing package uses its recorded outputs. Resuming a completed bake does not regenerate it.

Lighting reuse first checks the exact build key, then discovers existing workspace caches against the planned static scene and its actual material dependencies. Unrelated NPC assets therefore need not force a rebake. New cache metadata records scene inputs; legacy entries without those inputs are checked by output hashes and tile layout and explicitly reported as having unrecorded input compatibility. Ambiguous legacy matches are rebuilt. Shader reuse checks native Engine archives, tool binaries, seed/configuration files and repair code. Completed lighting and shader entries carry output SHA-256 hashes and are published only after successful completion; cancellation leaves the previous complete entry available. Existing workspace cache discovery does not require selecting its originating job.

Only baked voxel shards are reused from the lighting cache. Setup applies the lighting configuration to the **new** world archives, retaining that build's quests, NPCs, schedules and other world content. Packages receive independent copies of cached outputs. Cache entries occupy additional disk space; they can be removed when no build is running, at the cost of rebuilding those outputs next time. Dated jobs retain their own resume checkpoints.

To populate the cache from a completed older build, select **Import cache from older build…** on the Lighting screen and choose its dated folder. Setup verifies the saved lighting shard hashes and compiled shader package, then copies them into `cache/imported`. It does not copy old world archives, quests or NPC data. Incomplete or changed outputs are rejected, and the source build remains untouched.

Older outputs lack the new input fingerprint. At build review, setup therefore asks **Use imported outputs** or **Build current outputs** and shows the source folder. This is a choice for that build, not a permanent compatibility claim: the old lighting may differ if geometry or materials have changed. Only the matching lighting preset is offered; shaders with a different native source or compiler are rebuilt. Outputs with a current matching fingerprint take precedence. Selecting **Rebuild lighting and shaders** bypasses both kinds of cache. Importing by itself does not authorize use or launch a build.

The same function is available to scripts:

```powershell
.venv\Scripts\python.exe tools/setup_cache_import.py --build D:\BuildKCDMod\20261020-042542-d3827be8 --workspace D:\BuildKCDMod
```

Automated fixture tests cover import, explicit selection, reuse, invalidation, cancellation and preservation of new gameplay data. A full-map cache-hit timing comparison has not yet been run.

The bundled GPU binary currently targets `sm_120` (the tested development GPU). Other architectures or unsupported drivers use the CPU fallback; broader GPU compatibility has not been validated. The executable includes the GPU DLL, PTX, integrity metadata and OptiX license. Users do not need the CUDA toolkit to run setup.

A fresh map build reserves **200 GiB** in the build location and **64 GiB** for installation staging. Add the chosen bake allowance and another **10 GiB** for the optional movie. Requirements combine when build and game folders share a drive. Fishing alone needs approximately 1 GiB for build and staging. These are planning allowances, not final installed sizes. Existing build folders and older backups are not automatically removed. Successful map replacement retains no additional full-mod backup.

Setup v0.3.28 includes parallel tile baking and fresh NPC appearance performance improvements. See [performance evidence](status.md#performance-evidence) for the measured configuration and scope. The historical Buildings benchmark does not establish a full v0.3.28 build time, All (Forest) timing or installation time on your machine. CPU, storage, memory, shader compilation and selected coverage affect duration.

## Continue a failed setup

1. Keep the original job folder and its output.
2. Stop any older setup run for that job. Open this updated setup.
3. Go to **Activity → Restore / upgrade build…** (called **Resume failed setup…** in older builds).
4. Select the timestamped folder containing `job.json`, `package`, and `map-job`. Do not select `map-job`, `baking`, or the installed Mods folder.
5. Setup reads the saved options, verifies completed packages and resumes remaining operations. Watch the new attempt's log.

For a failure in optional baking, a completed map package from the current recipe is reused. A completed movie or selected bake is skipped when its package receipt confirms completion. Compatible completed bake tiles are verified and reused. Missing or damaged tiles are rebuilt. Interrupted scene planning restarts that planning phase.

**Completed packages from supported older recipes can be upgraded through Restore / upgrade build.** Setup v0.3.28 accepts receipts from recipes 3–61 for checking and migrates supported older packages to recipe 61. These are eligibility checks, not a guarantee that every saved package will pass validation. Select the original job folder containing `job.json`; a completed job is accepted too. Setup verifies the original package and stages the migration before replacing it. It upgrades saved source appearances through the normal converter and accounts for retained work when checking remaining build space. Direct installation requires the recipe expected by the setup you are running and does not perform migration. Incomplete world conversion may still need rebuilding. Keep the build workspace for [stale LOD repair](stale-section-repair.md).

If map conversion failed before a complete package existed, the map phase restarts. Resume does not checkpoint every individual world-converter stage. Partial outputs remain for diagnosis. Modified or incomplete packages are rejected rather than treated as successful.

Each attempt gets separate `resume-<timestamp>-<id>` logs under the original job. Old cancellation requests do not apply to a new attempt. Remaining disk-space checks exclude already completed world conversion. If the final installation failed, resume retries the verified installation transaction; a previously installed first mod may be staged again when recovering a two-mod run.

Use **Cancel safely** rather than terminating the process. Cancellation waits for the current safe work boundary; some operations take time to finish. The next attempt can reuse completed packages and compatible bake tiles. Optional movie and bake work is prepared separately so failure leaves the verified map package available.

## Enter the map or fish

- **KCD1 Map:** use **Launch mod** in the setup Library, then continue a Trosky save, speak to the coachman and choose **Travel to the Rattay region**. The retail menu also offers **New Game Sasau region (In Progress)** as an experimental start. Launching through the ordinary Steam entry uses the original retail Engine shaders. The custom launcher changes the shared shaders for the entire game session; other regions and mods still need compatibility testing.
- **KCD2 Fishing:** buy a rod from a general merchant after its stock refreshes. Having the rod in your inventory is enough. Face nearby water and use the contextual **Fish** action. Follow the casting and bite prompts; cancelling or moving ends fishing.

If the old map contains an embedded fishing test, standalone fishing installation is blocked. Build the clean map package before adding the separate fishing mod.

## Install existing packages

Choose **Install existing packages** and select the job's parent `package` folder, which contains `map` and/or `fishing`. The selected packages must match their receipts and the map must have completed the recipe required by that setup: 61 in v0.3.28, including its current appearance and content validation. Use **Restore / upgrade build** first for supported older packages. An older executable cannot install a newer-recipe package. The official Modding Tools are required for map installation even when compilation was already completed. Direct installation does not rerun conversion or change bake settings.

## Logs and reporting problems

Use **Open log** and **Open folder** in Activity. The main `setup.log` and `events.jsonl` record selected-mod operations. `map-job/setup.log` records world conversion and gameplay packaging. `stage-times.json` records measured stage durations. Resume attempts have separate logs; include both the original error and the new attempt when reporting a problem.

Bake errors identify the affected mesh and scene plan. Setup 0.3.3 fixes the reported unbound burned-fabric mesh by recording its default-material binding while retaining its geometry. Failed stages are reported as failed, rather than misleadingly logged as finished.

When filing an issue, include setup version, selected mods and bake mode, game version, error text and relevant logs. Do not upload game archives or your complete converted package.

## Installation, backups and removal

The destinations are `<KCD2>/Mods/kingdomcomegluemapper` and `<KCD2>/Mods/kcd2fishing`. Setup stages and verifies files before replacing recognized installations. Map replacement keeps rollback data only during the transaction, then removes it on success. Standalone Fishing still retains its previous installation and mod-order backup under `<KCD2>/GlueMapper Backups`. Modified or unknown installations block replacement. Retail game archives and saves are not overwritten.

To disable a mod, close the game, move its folder outside Mods, and remove its line from `mod_order.txt` if that file exists. Keep unrelated entries. Launch KCD2 through Steam to use its original Engine shaders. The linked `.kingdomcomegluemapper-runtime` folder is generated by Launch mod and can be refreshed by the launcher; it contains junctions to game assets, so do not use a cleanup tool that traverses junction targets.

Existing build folders, unsuccessful attempts, and backups from earlier versions are not automatically deleted. A successful map install no longer creates a permanent rollback copy; regenerate the desired version from source or keep a verified package when recovery is needed. Setup does not clear retail shader caches.

## Build from source

The reviewed source can migrate eligible completed recipe 3 through 50 packages
to recipe 51 without repeating world conversion. The full path still verifies
package receipts and required content, streaming and shader checks. Completed
compatible lighting is retained. Earlier migrations also update the campaign start guard, remove the old
horse-recovery hook, reconcile smart-area registrations, and remove compiled
smart-area geometry duplicates while preserving their inline shapes. These
area changes passed the user's Mod Tools return-travel test; retail and broader
save/companion coverage remain separate acceptance checks.

Only `kcd1_travel` is baked and retained in the final package. `kcd1_rataje` is
an intermediate conversion input; its packaged directory and level registration
are removed while the playable level ID remains unchanged. Successful bake and
upgrade swaps remove their temporary rollback directories. Existing historical
builds and checkpoints are not deleted automatically.

Use **Resume failed setup** in the new executable and select the original job
folder containing `job.json`. A safely cancelled or completed job can also be
selected. Stop the previous setup worker first. The upgrade runs before install;
choosing **Install packages** directly does not migrate an older recipe.

Recipe 11 also upgrades existing male `gm_person_*` fixed outfits through the
same native material converter used by clean builds. It extracts and repacks one
actor at a time on the build drive, uses ZIP32 shards, and updates only the actor
assets and their character/clothing material tables. It preserves actor identity,
quests, schedules, inventory registrations, and completed lighting bake metadata.
Already upgraded native outfits are skipped. Failed migration staging is removed;
the previous verified package remains available. Rendered appearance still needs
an in-game check after installation.

Recipe 13 also repairs world gameplay dependency packaging: it places the selected
native quest graph closure under the mod with literal, downward-only include
paths. This prevents the runtime XML reader from rejecting the campaign project
on unresolved parent-directory archive paths. Migration uses the selected game's
Scripts.pak and retains existing world assets and lighting.

Recipe 14 extends that dependency collection to inherited `utils.*` namespaces.
It packages referenced native utility modules recursively and preserves their
library instances, instead of assuming Barbora's parent scope exists in the
converted campaign. Recipe 13 packages are upgraded by the same collector.

Recipe 15 also collects implicit sibling dependencies (empty namespaces) and
short relative namespace references within utility libraries. Clean builds and
migrations validate custom node types against the packaged lexical scopes and
the selected game's built-in node definitions before accepting those graphs.

Recipe 16 restores source-authored NPC objective markers after all gameplay
stages finish. It binds active journal objectives to registered souls using the
original KCD1 quest records, including Father and Kunesh. Both clean builds and
resume migrations use this step; migration requires the configured KCD1 folder.
Editor graph coordinates are never used as world/map locations.

Recipe 17 translates the verified source `Utils.GiveItem` API to KCD2's
`ItemUtils.GiveItem`, including imported dialogue participant aliases. It also
compiles local objective completion predicates using the source expression and
preserves source carried inventories alongside NPC clothing and weapons.
Saved-package migration restores these same contracts for registered actors;
it does not refill live inventories or repair already-created save entities.
Test carried inventory changes with a fresh New Game. These repairs do not imply
complete quest-world conversion: quest-controlled shop layers and their ownership
and service lifecycle still require conversion and runtime validation.

Recipe 12 replaces the arrival-time debug-mode guard with a native `GameStart`
request and a separate region-readiness state. Both must be set before the
once-only campaign initializer runs. Initialization consumes the request before
starter equipment and quests are applied; arrival alone cannot request it.
Setup refreshes the campaign menu as well as the graph during migration, retaining
the normal native New Game operation and save-deletion protection. Tests cover
both event orders and repeated arrivals; runtime New Game and existing-save travel
must still be checked in-game. An old save whose initialization latch was already
closed is preserved, not reset: test the opening with a fresh New Game.

On 2026-10-18, the existing full-bake recipe 7 build was migrated to recipe 11
and installed through the rebuilt setup. Seven male appearances were upgraded,
the full bake was retained, and the user confirmed the installed result works
in-game. This is a bounded playtest confirmation, not exhaustive NPC coverage.

On Windows x64, Python 3.13 avoids compiling the pinned xatlas dependency. Building the GPU payload also requires Visual Studio C++ build tools and the CUDA toolkit; users of the finished setup do not need these build tools:

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-setup.txt
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
.venv/Scripts/python.exe -m unittest discover -s tests
./tools/build_voxel_cuda.ps1 -Optix
.venv/Scripts/python.exe tools/build_setup_exe.py
```

Python 3.14 also works when the Visual Studio C++ build tools are available to build `xatlas==0.0.11` from source. That version of xatlas has a Windows cp313 wheel but no cp314 wheel; a missing wheel is not a Python 3.14 incompatibility. The parallel CPU baker supports both Python versions.

Outputs include the EXE, SHA-256 checksum and frozen-runtime self-test report. GitHub Actions is manual-only; a push or release tag does not automatically run tests. Frozen checks validate included resources, native extensions, GPU payload hashes and a small CPU raster job. They also run a small OptiX bake against the CPU reference and report GPU availability explicitly; they do not substitute for a complete bake or in-game playtest.

See [launcher implementation details](setup-launcher.md) and [region travel](region-travel.md) for further scope and limitations. The release distributes setup code and interface assets, not converted game archives.

Recipe 18 also migrates supported source objective-update handlers so parent objectives and their map markers follow the original quest lifecycle.

Recipe 19 imports the source guard supply conversations and their actor/inventory dependencies, including authored Skalitz opening placements. Existing packages receive these through the same builders during upgrade. Original cinematic movement choreography remains outside this bounded conversion.

Recipes 30–33 include retail-compatible opening initialization, corrected KCD1-to-KCD2
LOD ratio encoding in both instances and vegetation groups, native vegetation grid
metadata, and retirement of obsolete ambient-audio substitutions. The LOD stage
records hashes to prevent double conversion on resume. The audio migration uses
original source control names, preserves legitimate village loops and surviving
entity identities, and removes obsolete references from shared audio boundaries.
Lighting data and geometry are retained. These migrations do not repair already
consumed quest events or rewrite saved games.

When a fresh shader build is on a different drive from the game, setup now uses
directory junctions for the read-only Data and Localization inputs. Shader sources
and compiler output remain in the private build folder. An existing compiler folder
that already contains copied files is retained; this change does not reclaim it
automatically. See the [0.3.8 release notes](releases/v0.3.8.md).
