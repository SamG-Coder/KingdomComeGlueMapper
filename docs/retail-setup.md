# Retail mod setup

The product target is: run setup with installed KCD1 and KCD2, generate the
conversion locally, launch the normal KCD2 executable, select **Play KDC1**,
then use native New Game / Save / Load / Continue for that campaign.

## Implemented first stage

The [Windows setup app](windows-setup.md) now orchestrates the world conversion
from both retail installations and wraps installation with progress and logging.
The commands below remain available for development and existing converted data.

`tools/setup_campaign.py` builds, verifies and installs a standard retail mod.
It reads the original opening quest and New Game graphs from **KCD1 retail
archives**, including numbered patch candidates. Neither the modding-tools
installation nor its SQL/reference database is a setup input. The tools can
still be used during development to understand formats.

This stage is a **loader and menu probe**, not the playable campaign. Its startup
script sets `campaignReady=false`. With `--menu`, setup generates a patched copy
of the user's retail UI and adds **Play KDC1** with a tooltip explaining that the
campaign is not ready. The entry stays disabled by default; a packaged campaign
can enable the experimental New Game route with `--start-probe`. It calls the
native New Game menu and selects the converted level. `--diagnostics` optionally
logs available APIs and UI events.

```powershell
python tools/setup_campaign.py build-probe `
  --kcd1 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance" `
  --kcd2 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2" `
  --output outputs/retail-probe-005 --menu

python tools/setup_campaign.py verify --package outputs/retail-probe-005

python tools/setup_campaign.py install `
  --package outputs/retail-probe-005 `
  --kcd2 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2"
```

Use a new output directory for each build. Existing installations are not
overwritten. For an update, close the game, preserve the owned installation
with the uninstall command, then install the new build:

```powershell
python tools/setup_campaign.py uninstall `
  --kcd2 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2" `
  --backup outputs/retail-probe-backup
```

The backup must be on the same drive and outside Mods. Other mods, load order
and saves are untouched. If `Mods/mod_order.txt` exists and excludes our modid,
installation reports that explicitly instead of changing the user's load order.

Generated files:

| File | Purpose |
| --- | --- |
| `mod.manifest` | Stable modid `kingdomcomegluemapper`, exact installed game-version requirement |
| `Data/kingdomcomegluemapper.pak` | Uncompressed ZIP, with the native `Scripts/Mods/<modid>.lua` startup hook |
| `campaign-audit.json` | Source hashes and override candidates, original graph nodes/edges, persistent-variable inventory and outstanding conversion gates |
| `gluemapper-install.json` | Package hashes, input installations, schema and build version |

Generated reports and packages remain local and ignored by Git. No game quest
scripts, UI binaries or assets are shipped in the source repository.

## Verified retail result, 8 October 2026

KCD2 **1.5.6**, launched from
`Bin/Win64MasterMasterSteamPGO/KingdomCome.exe` with **no arguments**, recorded:

```text
[Mod] 'mods/kingdomcomegluemapper' supports game version '1.5.6' explicitly, it will be enabled
Pak 'mods\kingdomcomegluemapper\data\kingdomcomegluemapper.pak' is opened, root: 'data\'
Loading lua init script for mod kingdomcomegluemapper ...
[GlueMapper] retail bootstrap loaded; campaignReady=false; version=0.1.0
```

Subsequent testing on retail 1.5.6 confirmed **Play KDC1 → Standard Mode** loads
`kcd1_rataje` and initializes the player. Campaign quest execution and save/load
round trips are still unverified.

After adding the source `spawnStart` as a native spawn point, a fresh retail
New Game was visually checked at ground level beside Henry's home in Skalitz.
The original source position is `728.86243,3411.5974,63.75`. This was tested with
the isolated `KingdomComeGlueMapperTest` profile; normal-profile saves were not
used. The source-only regression suite passes 94 tests, including spawn import,
repeat-update deduplication and the exact database table mount path.

The menu-enabled probe also recorded the root-menu event and addition of the
Play KDC1 entry. The user confirmed that the entry appears in the retail menu.
Setup generates `Menu.gfx` and its UI XML from the installed game; neither asset
is distributed in this repository. The patch preserves the native root function
and appends a lifecycle event used by the Lua menu hook. It rejects unrecognized
function bodies rather than patching an unknown layout. The initial-level CVar
is restored on return to the root menu. Compatibility with other mods replacing
the same UI remains unverified.

The opening audit contains 181 graph nodes, 148 edges and 109 objective nodes.
It selects patched New Game (`ipl_patch_010700.pak`) and opening quest graph
(`ipl_patch_010800.pak`). It retains three edges referencing node `0`; whether
these are sentinel links must be resolved before interpreting the graph.
Patch ordering currently uses whole-file revision order and rejects conflicting
same-revision candidates. Runtime equivalence of the resolved quest remains a
conversion validation gate, not something the audit establishes.

## Next integration gates

### Converted campaign bundle

`build-campaign` now packages an existing converter output into the retail mod:

```powershell
python tools/setup_campaign.py build-campaign `
  --kcd1 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance" `
  --kcd2 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2" `
  --converted-data "D:/SteamLibrary/steamapps/common/KCD2Mod/Data" `
  --source-level kcd1_world_v43 `
  --output outputs/retail-campaign-002 --diagnostics
```

`--converted-data` accepts a directory produced by the conversion stages; it
does not invoke or read an editor database. The example uses the existing local
development output. The Windows setup app creates its own private converted-data
directory by running the world conversion stages from both retail installations.

The bundle contains:

- `Data/Levels/kcd1_rataje/level.pak`, `terrain.pak` and `levelinfo.xml`, with
  stable level identity replacing the temporary probe name.
- `Data/kingdomcomegluemapper_world_NNN.pak`: transitively referenced imported
  geometry, materials, textures and streamed sidecars. Missing or ambiguous
  imported paths stop the build. Archives are split at 1 GiB / 50,000 entries:
  retail rejected the earlier single large archive. Native asset references
  require separate audit.
- A table patch registering `kcd1_rataje` as level ID `1000`, refusing a collision
  in the installed native level table and preserving native rows.
- `Data/kingdomcomegluemapper_sources.pak`: the four patched retail opening
  inputs under `GlueMapper/CampaignSource/`, isolated from native quest paths.
  These are source inputs, **not translated executable quests**.
- `campaign.json`: map path, intended New Game level, opening source provenance,
  imported asset hashes and explicit readiness gates.
- A native `SpawnPoint` in `objects_mission0.xml`, taking the original retail
  `spawnStart` position and rotation beside Henry's home. The generated name is
  `gluemapper_new_game_spawn`; original entity IDs are not reused. Source archive,
  entry, hash and transform are recorded in `campaign.json`. Rebuilding replaces
  the owned point without duplicating it; competing spawn points stop the build.
- Native level-local table containers and aliases at the retail database
  loader's mount path. Existing converted table records are preserved; missing
  containers are created without copying Trosecko NPC schedules or entity IDs.

The verified native route sets `wh_sys_BaseLevelId=1000` for Play KDC1 and invokes
the original New Game dispatcher. To enable it in an existing verified bundle,
close the game, then run:

```powershell
python tools/setup_campaign.py update-runtime `
  --package "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2/Mods/kingdomcomegluemapper" `
  --backup outputs/runtime-backup-next --start-probe
```

Choose a new backup directory each time. The command verifies the content,
updates the small runtime/level packages and receipt, and keeps rollback copies.
`refresh-runtime --package ... --output ... --start-probe` makes a separate full
bundle instead. Both paths preserve the table mount correction and native spawn.
Restart the game and select **Play KDC1 → Standard Mode** to exercise New Game.
This milestone adds no load-time teleport or replacement save system.

The fatal database error came from a mount mismatch: retail requested
`Data/Mods/<modid>/Data/Levels/kcd1_rataje/Tables/WeatherProfiles.xml`, but the
level archive mounted without the leading `Data/`. The root runtime archive now
exposes the packaged tables at that requested path. Runtime tracing confirmed
the weather XML opens successfully and world loading continues.

The first full local bundle collected 92,908 imported assets (17.50 GB of asset
payload), totaling 18.33 GB with the converted level and metadata. Generated
content stays local. The resulting 17 asset archives mount successfully in retail.

### Remaining gates

1. **Menu compatibility:** broaden testing of cancellation, campaign switching
   and other UI mods beyond the verified native New Game route.
2. **Campaign setup:** broaden compatibility and runtime validation of the new
   two-installation build pipeline. Full gameplay conversion remains separate.
3. **New Game and persistence:** establish native quest state, actor, inventory
   and trigger round trips before translating the opening quest. Separate fresh
   initialization from save restoration; never grant starter items on load.
4. **Original opening:** convert the retail quest behavior, entity links,
   schedules, dialogue, sounds and triggers. Resolve unsupported operations
   explicitly; do not replace the story with a handwritten approximation.
5. **Ready state:** enable Play KDC1 by default only after opening and save gates
   pass. The experimental start flag does not set `ready_to_play=true`. Existing
   KCD1 save migration remains separate from new KCD2 saves.

See [campaign specification](kcd1-campaign-start-spec.md) for the state and
dependency contracts.

Format references: Warhorse's [mod structure](https://warhorse.youtrack.cloud/articles/KM-A-3/Structure-of-a-Mod),
[manifest](https://warhorse.youtrack.cloud/articles/KM-A-57/Mod-Manifest) and
[publishing guide](https://warhorse.youtrack.cloud/articles/KM-A-58/Publishing-a-mod).
These articles were read through the official YouTrack API during implementation.
