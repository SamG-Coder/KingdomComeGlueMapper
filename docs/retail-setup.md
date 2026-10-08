# Retail mod setup

The product target is: run setup with installed KCD1 and KCD2, generate the
conversion locally, launch the normal KCD2 executable, select **Play KDC1**,
then use native New Game / Save / Load / Continue for that campaign.

## Implemented first stage

`tools/setup_campaign.py` builds, verifies and installs a standard retail mod.
It reads the original opening quest and New Game graphs from **KCD1 retail
archives**, including numbered patch candidates. Neither the modding-tools
installation nor its SQL/reference database is a setup input. The tools can
still be used during development to understand formats.

This stage is a **loader and menu probe**, not the playable campaign. Its startup
script sets `campaignReady=false`. With `--menu`, setup generates a patched copy
of the user's retail UI and adds **Play KDC1** with a tooltip explaining that the
campaign is not ready. The entry stays disabled. It does not replace KCD2's New
Game or modify saves. `--diagnostics` optionally logs available APIs and UI events.

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

This proves retail package mounting and script execution. It does **not** prove
the converted level loads in retail, campaign logic runs, or custom state saves.

The menu-enabled probe also recorded the root-menu event and addition of the
Play KDC1 entry. The user confirmed that the entry appears in the retail menu.
Setup generates `Menu.gfx` and its UI XML from the installed game; neither asset
is distributed in this repository. The patch preserves the native root function
and appends a lifecycle event used by the Lua menu hook. It rejects unrecognized
function bodies rather than patching an unknown layout. Submenu return behavior
and compatibility with other mods that replace the same UI remain unverified.

The opening audit contains 181 graph nodes, 148 edges and 109 objective nodes.
It selects patched New Game (`ipl_patch_010700.pak`) and opening quest graph
(`ipl_patch_010800.pak`). It retains three edges referencing node `0`; whether
these are sentinel links must be resolved before interpreting the graph.
Patch ordering currently uses whole-file revision order and rejects conflicting
same-revision candidates. Runtime equivalence of the resolved quest remains a
conversion validation gate, not something the audit establishes.

## Next integration gates

1. **Menu action:** the entry is visible through a generated root-menu hook and
   `UIAction.CallFunction`. Connect it to the native New Game lifecycle after
   identifying the campaign dispatcher; visible UI does not establish this link.
2. **Campaign package:** consolidate the successful map/NPC/material conversions
   into a dependency-complete build with a stable level identity. Current loose
   probe namespaces are not a release package.
3. **New Game and persistence:** establish native quest state, actor, inventory
   and trigger round trips before translating the opening quest. Separate fresh
   initialization from save restoration; never grant starter items on load.
4. **Original opening:** convert the retail quest behavior, entity links,
   schedules, dialogue, sounds and triggers. Resolve unsupported operations
   explicitly; do not replace the story with a handwritten approximation.
5. **Ready state:** enable Play KDC1 only after packaged world, opening and save
   gates pass. Existing KCD1 save migration remains separate from new KCD2 saves.

See [campaign specification](kcd1-campaign-start-spec.md) for the state and
dependency contracts.

Format references: Warhorse's [mod structure](https://warhorse.youtrack.cloud/articles/KM-A-3/Structure-of-a-Mod),
[manifest](https://warhorse.youtrack.cloud/articles/KM-A-57/Mod-Manifest) and
[publishing guide](https://warhorse.youtrack.cloud/articles/KM-A-58/Publishing-a-mod).
These articles were read through the official YouTrack API during implementation.
