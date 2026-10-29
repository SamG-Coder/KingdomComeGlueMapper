<div align="center">

# KingdomComeGlueMapper

**Explore the world of Kingdom Come: Deliverance in Kingdom Come: Deliverance II.**

An experimental world and gameplay converter built around the assets in your installed games.

[![Platform](https://img.shields.io/badge/platform-Windows-blue)](#getting-started)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#project-status)
[![License: MIT](https://img.shields.io/badge/code-MIT-green)](LICENSE)

[Website](https://samg-coder.github.io/KingdomComeGlueMapper/) · [Download setup](https://github.com/SamG-Coder/KingdomComeGlueMapper/releases) · [Installation guide](docs/windows-setup.md) · [Feature checklist](#feature-checklist) · [Report an issue](https://github.com/SamG-Coder/KingdomComeGlueMapper/issues)

</div>

![Imported countryside with grassy hills, trees and a distant windmill in KCD2](docs/images/countryside-2026-10.jpg)

*Development screenshot supplied on 22 October 2026. The exact build was not identified; this visual snapshot does not establish release-wide gameplay acceptance. See the checklist below for scope.*

<details>
<summary>More development screenshots</summary>

![Woodland path, trees and distant hills](docs/images/woodland-path-2026-10.jpg)

![Sunlight through the forest canopy and undergrowth](docs/images/forest-canopy-2026-10.jpg)

The full frame and original game HUD are retained in these web-sized copies.
See [screenshot provenance](docs/images/screenshots-2026-10.md).

</details>

Follow development on [YouTube](https://www.youtube.com/@SamGCoder) and
[X / @SamGCoder](https://x.com/SamGCoder).

## The project

KingdomComeGlueMapper reads compiled KCD1 and KCD2 archives, converts the original landscape and assets, and builds a separate region for KCD2. The combined setup packages the world, coach travel and supported gameplay conversions into one mod.

The goal is to preserve KCD1's authored world while using compatible KCD2 runtime systems. Buildings and terrain retain their source placement; interactions receive native contracts where implemented. Wildlife uses **KCD2 models, animations and AI at converted KCD1 habitats**.

- **A world to explore:** terrain, buildings, interiors, vegetation, roads, streams and original map detail.
- **Growing gameplay support:** seating, ladders, cooking, washing, harvesting, pickups, doors, selected services and wildlife.
- **Local asset conversion:** generated game content comes from your own installations and stays on your machine.
- **Inspectable tooling:** Python converters, dependency reports, regression tests and focused in-game experiments.

## Project status

**The main Rataje world is imported geographically. Complete KCD1 gameplay, campaign progression and every interactive object are still being converted.** Separate source maps and every DLC or alternate quest state are not covered by a whole-game completion claim.

The checklist describes source implementation, with dated development-build observations below. It is not a release manifest or a whole-game acceptance result. See the [release, source and runtime status](docs/status.md) for the **22 October 2026 source review**, newer gameplay work and its remaining checks. Historical counts retain their original observation dates.

The normal travel route is **Launch mod → Continue a Trosky save → coachman → Travel to the Rattay region**. Setup also connects the bounded Skalitz opening and selected gameplay conversions to its build recipe. This does not complete the KCD1 campaign. KCD1 saves are not converted.

## Getting started

### Windows setup

1. Download **KingdomComeGlueMapper-Setup-0.3.28-win64.exe** from the **Assets** section of the [0.3.28 prerelease](https://github.com/SamG-Coder/KingdomComeGlueMapper/releases/tag/v0.3.28), not GitHub's source-code ZIP. Python is bundled; Microsoft Edge WebView2 is required. The release also includes `SHA256SUMS.txt` and `self-test.json`. See the [0.3.28 release notes](docs/releases/v0.3.28.md) for changes since the last published release, contributor credits and validation, and the [KCD1:MP guide](docs/kcd1-mp-setup.md) for the separate experimental multiplayer export.
2. Select **KCD1 Map**, **KCD2 Fishing**, or both. KCD1 Map needs both games and the **official KCD2 Modding Tools from Steam**; fishing alone needs KCD2. The supported KCD2 version is **1.5.6**.
3. Choose game locations and a build folder outside the games. A fresh map build reserves **200 GiB**, plus **64 GiB** for installation staging; these combine on the same drive.
4. Choose **No Baking**, **Buildings** (+30 GiB working space), or **All (Forest)** (+60 GiB). The optional opening movie needs an external RAD encoder, FFmpeg/FFprobe and another 10 GiB; it currently replaces the shared New Game intro, including KCD2's.
5. Close the games, review settings and start. Activity lists pending steps, progress and logs.
6. For KCD1 Map, keep Steam running and choose **Launch mod** in the setup Library to load its compiled shaders. Continue a Trosky save and use the coachman's **Travel to the Rattay region** option. The retail **New Game Sasau region (In Progress)** menu entry is experimental. For fishing, buy a rod from a general merchant after stock refresh, carry it, face nearby water and choose **Fish**.

**Updating or resuming?** Choose **Activity → Restore / upgrade build…** (called **Resume failed setup…** in older builds) and select the original job folder containing `job.json`. Supported completed recipes migrate through the shared upgrader without a full map rebuild. Verified current packages and compatible finished lighting tiles are reused. Stale LOD validation can be [repaired as a single section](docs/stale-section-repair.md) using verified saved build data, without rerunning completed gameplay or lighting stages. The clothing repairs are part of normal Setup conversion and saved appearance upgrades. Keep the original build workspace available. If map conversion never produced a complete package, that phase restarts. Logs and partial outputs are retained.

Current source also upgrades [introduction and training handoffs](docs/source-opening-training-upgrade.md), [pub cleaning and resting](docs/source-pub-work-upgrade.md), [source-selected dice work](docs/source-gambler-conversion.md), and [supported voiced market calls](docs/work-speech-coverage.md). These use authored actor assignments, work windows and contacts. The [work ownership audit](docs/source-work-ownership.md) records assignments belonging to other campaign profiles and unresolved coverage. The guides distinguish package validation from the remaining in-game checks and source behaviors awaiting conversion.

Setup v0.3.28 includes parallel tile baking and fresh NPC appearance performance improvements. See the [performance evidence and limits](docs/status.md#performance-evidence) for hardware, measured stages and release scope. These measurements are not a duration guarantee for the downloaded installer, another bake mode or your machine.

See the [Windows setup guide](docs/windows-setup.md) for requirements, instructions, recovery and rollback, and the [launcher details](docs/setup-launcher.md) for each mode and current limitations.

### Run the current source

Use Windows and Python 3.13 for the setup development environment:

```powershell
git clone https://github.com/SamG-Coder/KingdomComeGlueMapper.git
cd KingdomComeGlueMapper
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-setup.txt
.venv/Scripts/python.exe tools/setup_app.py
```

Python 3.14 also works with the Visual Studio C++ build tools installed:
the pinned `xatlas==0.0.11` has no cp314 wheel and must build from source.
Python 3.13 avoids that compilation through its Windows wheel. See
[contributing](CONTRIBUTING.md) for setup, local tests and issue reports.

To build a distributable setup executable:

```powershell
.venv/Scripts/python.exe tools/build_setup_exe.py
```

The executable and self-test report are written to `outputs/release`. Build its GPU payload first as described in the [Windows setup guide](docs/windows-setup.md#build-from-source). KCD1 Map builds, installs, and resumes require the official KCD2 Modding Tools. Older `build_*_probe.py` commands are focused experiments with specific baseline requirements, not substitutes for the combined setup workflow.

## Feature checklist

**How to read this checklist:** a checked item means the described conversion exists in the current code. It does **not** mean every instance has passed in-game acceptance. Unchecked items are remaining work, including validation of implemented features. Runtime evidence and limitations are stated separately.

### World, terrain and rendering

- [x] Convert compiled KCD1 terrain into the target terrain format, preserving height and surface assignments.
- [x] Validate terrain samples, sector offsets and shared boundaries.
- [x] Convert terrain materials and resolve texture families, streaming mips and available HD overrides.
- [x] Import individual vegetation, instanced rocks/clutter and merged grass/ground cover.
- [x] Import static buildings, interiors, props and supported level-local landscape geometry.
- [x] Import initial/shared-state roads and decals.
- [x] Repair legacy static-brush registration flags that hid ground meshes, steps and rocks.
- [x] Convert building material features and repair supported secondary-UV blend inputs.
- [x] Convert water volumes, stream surfaces and their material dependencies.
- [x] Exclude explicitly later-state water from the initial world.
- [x] Compensate the KCD1/KCD2 animation time-base difference for `Waterfall` shader materials.
- [x] Convert interior probes, reflex lights, clipping associations and opacity voxels for basic GI.
- [x] Register imported map imagery, detail layers and location information.
- [x] Convert authored designer collision and barrier volumes with their KCD1 surface types, for the initial-world layers.
- [ ] Finish shallow mud-puddle surface appearance over imported ground.
- [ ] Complete broad roof/interior lighting, probe-boundary and material-fidelity validation.
- [ ] Validate collision, LOD transitions, streaming and traversal across the entire map.
- [x] Convert supported initial-world particles, fog and ambient audio across the main KCD1 map, using native KCD2 effects and sound controls.
- [x] Import original KCD1 adaptive music and bind registered settlement, inn, countryside, river and cave areas to the native KCD2 music system.
- [ ] Validate all music transitions by listening, including combat/dice outcomes, native-world return travel and remaining quest-driven states.
- [ ] Validate environmental appearance, audibility and boundary transitions throughout every region; restore unsupported legacy mixer behavior where a suitable native contract exists.
- [ ] Reconstruct quest-driven layer activation instead of selecting only the supported initial world.
- [ ] Extend conversion and acceptance to separate source maps and remaining DLC/world states.

**Evidence:** terrain/buildings/vegetation and selected ground repairs have in-game confirmation. Water-layer repair preserves retained records, and the flow-speed correction targets `Waterfall` materials specifically. It is not a universal adjustment to every water shader. Lighting conversion exists, but full visual parity remains open.

Interior exposure adaptation is corrected across the imported map's time-of-day profiles, with user confirmation in a sampled house. Broader lighting validation remains open; see [interior lighting](docs/interior-lighting.md).

**Environment coverage:** the installed conversion includes 1,307 particle emitters, 21 fog volumes, 1,355 audio entities and 931 linked audio boundaries across the main map's selected initial world. Missing KCD1 sounds and reverbs use explicit native KCD2 equivalents; these are semantic substitutions, not identical recordings. The corrected build reaches gameplay, resolves all 1,190 nonempty imported play-trigger references in the live engine, and received user confirmation on 13 October 2026. This is map-wide conversion coverage, not inspection of every individual effect. Quest/later-state layers and separate maps remain outside this pass. See the [environment conversion audit](docs/environment-conversion.md) for exclusions, validation and the reserved player-ID fix.

Sources: [terrain converter](tools/compiled_terrain.py), [static assets](tools/static_assets.py), [brush repair](tools/brush_registration.py), [water selection](tools/water_layers.py), [water timing](tools/water_animation.py), [lighting](tools/world_lighting.py), [voxel packaging](tools/world_voxels.py).

**Music coverage:** 119 original KCD1 scenes and 80 area bindings are installed for the main map's supported initial world. Runtime probes verify Skalitz, its inn, countryside, Rattay, Talmberg and leaving the imported-world boundary. Original town, pub and countryside scene playback is visible in the native debug overlay. Full listening acceptance, quest music and native-level return travel remain open. See the [music conversion audit](docs/music-conversion.md).

### Everyday interactions and items

**Container coverage:** the installed development build registers 2,769 converted stashes and preserves nine shared personal chests. All 174 generated loot tables include registered item names and GUIDs (2,208 entries); the user confirmed starting-home fruit sacks work after this fix. Containers are temporarily unowned. Merchant/NPC-generated stock, campaign transfers, wearable fit and persistence remain separate acceptance work. See the [container conversion audit](docs/container-conversion.md).

- [x] Convert authored chairs and benches to native seating contracts.
- [x] Convert supported cooking fireplaces/pots to native player interactions and effects.
- [x] Convert authored washing troughs, including helper alignment and cleaning effects.
- [x] Register supported imported vegetation for native herb harvesting.
- [x] Convert supported loose items and initially populated item slots into native pickups.
- [x] Convert authored personal chests to persistent shared player storage.
- [x] Convert active architectural doors, including native animation contracts, locks and keys.
- [x] Add the selected Rattay inn rental, bed and room-service contract.
- [x] Stage and install native storage for all 2,769 remaining initial-world stashes, including chests, sacks, bags, baskets, shelves and supply piles.
- [x] Convert 174 authored inventory presets and resolve their 589 item types, including 312 additional KCD1 wearable components.
- [ ] Validate locks, keys, model changes, replenishment and save/load across the converted containers; restore ownership and theft rules when homes/NPCs are converted.
- [x] Include fish traps, water/fish baskets and beehives in the native storage conversion.
- [ ] Validate their specialised behaviour, replenishment and interaction effects in game.
- [ ] Convert bird nests, contents, physics and associated sounds.
- [ ] Convert digging spots, graves and buried sacks, including tools, contents and dug-state persistence.
- [x] Map all 1,387 active source beds to native sleeping contracts, including ground beds, shelters, tents and sleeping benches; preserve existing rental and miller beds.
- [ ] Restore original home/NPC access rules for newly converted beds and validate every bed family, approach side and save/load behaviour. New beds are temporarily usable without ownership restrictions.
- [x] Convert all 113 ladders selected for the initial world to native climbing entities, preserving meshes, heights and approach settings.
- [ ] Validate top/bottom entry and exit at every ladder; convert the 26 deferred ladders when their quest layers are supported.
- [ ] Convert grindstone instances and verify sharpening effects.
- [ ] Convert alchemy benches and their complete activity contracts.
- [ ] Convert archery-target activities and dice-game locations.
- [ ] Complete book/recipe rewards, unmapped item effects and campaign-dependent item behaviour.
- [ ] Verify interaction state, item quantities and persistence after save/load and return travel.

**Evidence:** seating, herb picking, washing, loose-item pickup and the selected rental/bed fixes have sampled user gameplay confirmation. The inspected development level contains 274 washing triggers and 303 cooking groups; the pickup conversion report records 1,773 placements. These counts are coverage snapshots, not individual acceptance tests.

World beds: the development build adds 1,385 sleep contracts and preserves the two existing rental/miller beds. A sampled raised bed completed a one-hour native sleep cycle; a ground bed opened the native sleep menu with its lower comfort rating. Other families, placement clearance and save/load remain acceptance checks. See the [bed conversion evidence](docs/world-beds.md).

Ladders: the installed development build contains 113 native ladder entities with their static duplicates removed. The user confirmed climbing works at the sampled test location on 13 October 2026. Other locations and NPC climbing remain separate acceptance checks.

Sources: [chairs](tools/chairs.py), [benches](tools/benches.py), [cooking](tools/cooking_pots.py), [washing](tools/washing_troughs.py), [herbs](tools/herb_pickup.py), [pickups](tools/world_items.py), [player chests](tools/player_chests.py), [doors](tools/world_doors.py), [ladders](tools/ladders.py), [rental](tools/room_rental.py).

### Wildlife and population

- [x] Map supported KCD1 wildlife sites to native KCD2 animal definitions.
- [x] Convert boar, red-deer stag, red-deer doe and roe-buck spawn sites.
- [x] Convert forest/dynamic hare-area polygons and authored population overrides.
- [x] Remap native scheduler dependencies, area links and positioning delegates.
- [x] Register **229 wildlife sites**, configuring **752 animals** across supported populations.
- [x] Package navigation for the imported destination.
- [x] Provide character conversion and selected NPC placement, dialogue and activity integrations.
- [ ] Convert remaining regional wildlife contracts and livestock populations.
- [ ] Improve sparse hare encounters; the distributed-habitat experiment was rolled back after a loading crash. See the [regional audit](docs/wildlife-conversion.md#regional-audit-and-deferred-hare-experiment--13-october-2026).
- [ ] Restore ambient fish groups and remaining ambient animals.
- [ ] Expand NPC population, household ownership and daily routines across the world.
- [ ] Verify wildlife hunting/loot, fractional-day respawn handling and save/load persistence.
- [ ] Validate navigation and activity selection at every habitat and settlement.

**Evidence:** runtime sampling found native boar, hare, red-deer doe/stag and roe-buck actors. Two boars changed position between samples. The 752 figure is configured population, not a simultaneous active-actor count. Hare areas use native regional populations rather than an exact reproduction of KCD1's player-centred spawning.

Sources: [wildlife converter](tools/wildlife.py), [wildlife scope and validation](docs/wildlife-conversion.md), [navigation](tools/campaign_navigation.py), [character conversion](docs/character-upgrader.md).

### Travel, services and campaign

- [x] Build world conversion and coach travel into one installable mod.
- [x] Register the imported destination and native level/gameplay lifecycle.
- [x] Add selected Rattay, miller and horse-trader service integrations.
- [x] Register a bounded set of native open-world gameplay graphs and audit dependencies.
- [x] Provide source quest, dialogue and campaign dependency inspection/conversion tooling.
- [ ] Complete companion transfer and persistence acceptance through repeated round trips.
- [ ] Complete haggling and broader merchant/service coverage.
- [ ] Bind destination guards, crime, jail, punishment and escape locations.
- [ ] Convert teachers, bathhouses and remaining regional services.
- [ ] Complete original KCD1 quests, cinematics, voice/timing and campaign progression.
- [ ] Validate campaign-wide saving/loading and cross-region state preservation.
- [ ] Provide KCD1 save migration; no existing-save conversion is currently supplied.

**Evidence:** coach access, map display and selected retail services have recorded gameplay confirmation. Registering native combat, shop, repair or sharpening graphs does not establish every actor or world object is wired to them. The full original campaign remains unfinished.

Sources: [combined setup backend](tools/desktop_setup.py), [region builder](tools/region_travel.py), [gameplay graph registration](tools/world_gameplay_basis.py), [campaign tools](tools/setup_campaign.py).

### Quests and the original New Game opening

- [x] Trace the original New Game entry and convert the opening movie for a development launch; the movie stage has user acceptance.
- [x] Import the mother's original voice recordings, dialogue choices and character appearance into a native dialogue scene.
- [x] Convert source scene markers into native dialogue-entry placement; both participants remain inside the house through the tested two-choice conversation and return to gameplay.
- [x] Resolve the quest-graph loading stall and reconnect original objective callbacks and stat-reward bindings.
- [x] Register original opening quest text and bind the quest to its native destination region; journal text and region have in-game acceptance.
- [x] Add the development menu entry **New Game Debug Sasau region** below **New Game**, routing to the installed converted opening; user-confirmed menu operation.
- [ ] Verify all character-stat choice branches and prevention of repeated rewards.
- [ ] Restore remaining original gestures, lip-sync and starting-player equipment; validate every choice branch.
- [ ] Verify the complete movie → conversation → quest gameplay sequence, including movie skipping and save/load.
- [ ] Convert subsequent main, side and DLC quests with their actors, objectives, world changes, rewards and persistence.

**Current boundary:** the development test loads all 109 source objective states, awards both Speech choices, advances the original opening objectives and returns to gameplay with correct actor placement. Speech 7 survived a quicksave/reload without replaying the opening conversation. Opening journal text and region are now confirmed in game. Other stat branches, quest markers and subsequent interactions remain unverified; this is not a completed character-creation or campaign conversion. The ordinary setup still uses coach travel. See the [quest conversion checklist](docs/quest-checklist.md) for implementation milestones and per-quest acceptance requirements.

### Packaging and quality

- [x] Detect Steam installations and validate supported game versions.
- [x] Offer package-only builds and installation of an existing verified package.
- [x] Stage installation with receipt checks, backups and rollback handling.
- [x] Retain build logs, progress events and conversion reports.
- [x] Run asset-free regression tests and CLI checks in Windows CI.
- [x] Build a Windows setup executable with a bundled-runtime self-test.
- [ ] Run a fresh complete build and retail acceptance pass for each release candidate.
- [ ] Complete whole-map visual and gameplay acceptance, including persistence and mod compatibility.

## Current world-object audit

The following snapshot compares source mission/layer entities selected by the converter's initial-layer filter with the installed development level on **13 October 2026**. Source identities are deduplicated. These are conversion targets, not instructions to force every quest-state object into the live world.

| Area | Source audit | Current conversion gap |
|---|---:|---|
| Storage | 2,778 `Stash` entities | 2,769 converted stashes registered at runtime; nine shared personal chests preserved. Sampled storage/fruit-loot tests passed; ownership, special-service contents and persistence remain open. |
| Fish traps | 63 stashes using three fish-trap model types | Included in native storage conversion; water/fish baskets are additional variants. Specialised behaviour and replenishment remain unverified. |
| Nests | 103 | No converted nest registrations in the inspected level. |
| Digging | 79 holes: 63 grave pits and 16 buried sacks | No converted digging registrations. |
| Ambient fish | 41 fish-group entities | Separate from trap/container interaction; not converted. |
| Ladders | 113 | Converted to native climbing entities; sampled player test confirmed. Another 26 are deferred with inactive layers. |
| Grindstones | 11 | Global sharpening service registration does not activate these instances. |
| Alchemy benches | 7 | Complete native activity conversion remains. |
| Shooting targets / dice cups | 8 / 13 | Activity and participant conversion remains. |
| Beds | 1,387 | 1,385 new native sleep contracts plus two preserved existing beds; 438 inactive-layer beds remain deferred. Family alignment and persistence validation remain. |

Some KCD2 systems use different entity classes, so class counts alone are not proof of missing functionality. These gaps were cross-checked against the converter implementations. Counts describe the inspected source/build, not every game version or a published release guarantee.

## Next priorities

1. **Container acceptance and special storage:** verify item transfer, locks, phase changes, wearable fit, replenishment and save/load; complete merchant/NPC-generated stock and campaign-driven storage contracts.
2. **Nests and digging:** add native interactions, contents, effects and persistent state.
3. **World activities:** validate bed alignment and restore ownership; extend sharpening, alchemy and minigames; validate ladder access throughout the world.
4. **Population and ownership:** expand NPCs, livestock, homes, schedules and regional services.
5. **World parity and persistence:** close visual gaps and verify save/load, travel, respawn and quest-state transitions.

## Architecture and validation

```text
Installed KCD1 archives + installed KCD2 reference assets
                         |
           Terrain / geometry / material conversion
                         |
          Native gameplay contracts and dependencies
                         |
             World + travel package verification
                         |
             Mods/kingdomcomegluemapper
```

The setup's conversion plan builds the world, packages its dependencies, calls the region builder for gameplay/travel, and combines the result. Source transforms and identities are retained or explicitly remapped; unsupported dependencies are reported rather than assumed compatible.

| Entry point | Responsibility |
|---|---|
| [setup_app.py](tools/setup_app.py) / [desktop_setup.py](tools/desktop_setup.py) | Setup interface, preflight, build orchestration and installation. |
| [setup_campaign.py](tools/setup_campaign.py) | World packaging, registration and campaign-oriented developer commands. |
| [region_travel.py](tools/region_travel.py) | Destination lifecycle, travel, gameplay converters, lighting and wildlife. |
| [travel_bridge_package.py](tools/travel_bridge_package.py) | Combine world/travel packages and apply supported compatibility repairs. |
| [tests](tests) / [CI workflow](.github/workflows/validate.yml) | Regression checks, Python compilation and selected CLI entry points. |

Run the test suite from the source environment:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

The **Validate tooling** workflow checks Python 3.11 and 3.14 on Windows. It runs
on manual dispatch (Actions → *Validate tooling* → *Run workflow*), not on push,
so it does not gate a pull request; run the suite locally before opening one. It
does not have the games installed and cannot verify appearance or gameplay. Local
binary checks, frozen-executable checks, development-client tests and retail
gameplay are separate validation steps. The Python 3.14 environment needs a
C++ toolchain for xatlas, as described under
[Run the current source](#run-the-current-source).

### Developer tools

The [Quest & NPC Tests emulator](tools/quest_lab/README.md) runs converted quest
and controller logic offline, with deterministic timers, explicit engine
outcomes, save/replay, and searchable HTML/JSON/JUnit reports. Start with
`.venv/Scripts/python.exe tools/quest_lab_test.py demo --output outputs/quest-lab-demo`.
It is separate from consumer Setup; unsupported operations remain visible and
native gameplay still requires engine verification.

The [Clothing & Body Test workbench](tools/clothing_lab/README.md) provides a standalone 3D viewer and repeatable audits for male/female clothing, layer combinations, and all KCD1 soul records. It is a developer tool, separate from the consumer setup application. Launch it with `.venv/Scripts/python.exe tools/clothing_lab_app.py`.

## Documentation and issue reports

- [Setup, installation and rollback](docs/windows-setup.md)
- [Wildlife conversion and remaining runtime checks](docs/wildlife-conversion.md)
- [World pickups](docs/world-pickups.md) · [Herb harvesting](docs/herb-pickup.md)
- [Washing troughs](docs/washing-troughs-test.md) · [Room rental](docs/room-rental-test.md) · [World beds](docs/world-beds.md)
- [Gameplay-system dependencies](docs/world-gameplay-basis.md)
- [Static-brush visibility repair](docs/brush-registration.md) · [Water-layer selection](docs/water-layer-selection.md)
- [Building material investigation](docs/building-material-audit.md) · [Water animation compatibility](docs/water-animation-compatibility.md)
- [Historical world inventory](docs/world-import-checklist.md) · [Development notes](docs/development-notes.md)

Historical documents contain dated experiment results and commands. Use this checklist for current scope, the current code for implementation, and release notes for a particular downloaded build.

For a useful [issue report](https://github.com/SamG-Coder/KingdomComeGlueMapper/issues), include the converter commit/release, game version, location or coordinates, a screenshot and reproduction steps. State whether the failure concerns appearance, collision, interaction or persistence. Attach relevant log excerpts or report files without distributing game archives.

Known visual follow-up: [shallow mud puddles lose their wet surface over imported ground (#18)](https://github.com/SamG-Coder/KingdomComeGlueMapper/issues/18). The physical-surface correction and the missing wet appearance are separate issues.

## License and game content

The original tooling is released under the [MIT license](LICENSE). Game assets, trademarks and screenshots belong to their respective rights holders and are not covered by that code license. Third-party tool notices are retained under [third_party](third_party).

No game archives, extracted textures, meshes or generated world packages are distributed by this repository. Conversion requires your own installed games. KingdomComeGlueMapper is an independent experimental project, not an official Warhorse Studios or publisher release.
