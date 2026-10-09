# KDC1 region travel

This separate experimental overlay adds **Travel to KDC1** to the existing Trosky
driver's dialogue, plus a return coachman near KDC1's epilogue departure camp. The intended
journey is **Trosky → KDC1 → Trosky with the current KDC2 character**.

The builder reads the native `LevelSwitch` table, finishes the dialogue, calls
native `PassLongTime`, then `wh::game::SwitchLevel`, and registers a separate
`kcd1_travel` level. Its destination now includes a native `LevelHolder`, a
Skald `Level` module and native world AI services. The return station remains
resident; optional station streaming is disabled in normal builds.
It does not call New Game, recreate Henry, assign starting equipment, or execute
the unfinished KDC1 opening scripts. Native transfer owns character persistence;
inventory, horse, quest and save preservation still require a retail round trip.

## Build

```powershell
python tools/region_travel.py `
  --kcd1 'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance' `
  --kcd2 'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2' `
  --world 'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2/Mods/kingdomcomegluemapper/Data/Levels/kcd1_rataje' `
  --output outputs/region-travel-new
```

Use a fresh output directory. The input must be a static converted world, without
campaign Concept or QuestObject entities. The existing `kingdomcomegluemapper`
asset package must stay installed: this overlay references its converted models
and materials. It is not yet a standalone setup release.

The generated `gluemappertravel` folder is a normal mod folder. It supplies a
Trosecko level archive preserving native entries and adding a station, plus a
separate destination archive. Do not combine it with another mod overriding
Trosecko's mission, links or preprocessing data without merging them.

The return station is derived from `q_epilogue`'s
`QuestPlace[horseParkPtacek]` link, not an estimated map coordinate. Its marker
is `camphorsepark_ptacek` at approximately `(3831.58, 1787.66, 127.38)`.
The Trosky marker is the installed game's `startingPoint_trosecko`, approximately
`(2645.74, 1520.34, 87.20)`. Each cart is offset beside the arrival marker;
ground clearance and walking access require inspection in game.

## Acceptance test still required

1. Back up an existing Trosky save and record Henry's equipment, inventory,
   money, horse, horse inventory and quest progress. Tap **X** to confirm the
   horse responds in Trosky; holding X calls the dog with the user's bindings.
2. Load it in ordinary retail KDC2, talk to the native Trosky coachman and choose **Travel to KDC1**.
3. Check arrival, collision, equipment, inventory and the original horse using
   **X**. Check that the horse stands on the road clear of the cart. Save in KDC1,
   restart the game, and load that save.
4. Talk to the return coachman and use **Travel to Trosky**, compare the recorded state, and test another round
   trip to catch persistent interaction-state failures.
5. Save and reload in Trosky. Confirm existing regional travel still works.

Build and unit-test success do not establish these results. The report therefore
records `runtime_verified=false` and `round_trip_verified=false` until observed.
There are no new voiced cart-driver lines in this first interaction version.
The added driver choice is free, retains the native horse/crime/player-condition
guards, and waits for the dialogue to finish before switching levels. The original
Kuttenberg destination and payment dialogue are preserved. English UI localization
is included; the new dialogue choice still requires in-game confirmation.
KDC1 quest progression is outside this travel-only overlay.

## Horse investigation, 9 October 2026

User-confirmed: outbound coachman travel and the inventory now work. Pisek Lad,
its equipment and saddle inventory remain visible in the destination inventory.
Calling the horse still failed with the v6 package. Adding the native horse
scheduler proxy and default smart object alone did not fix it.

The static map used by v6 omitted all navigation: no recast.pak, ubernav.tmm or
areasmission0.bai. The builder now packages the original Rataje navigation,
including state variants. It checks source mesh settings against installed KDC2,
every indexed tile's offset, extent, coordinates and version, the declared tile
count, and the graph version. The observed base tile format is version 14 in both
games; the navigation graph version is 38. No target-region geometry is reused.

This repairs a packaging omission; it does not prove companion transfer works.
The runtime must still load these files and allow the existing owned horse to
answer a whistle, approach and be mounted. Quest-dependent navigation state
selection and graph semantics remain unverified. Do not create a replacement
horse or enable SpawnOnCall to mask the failure; preserve the owned horse and gear.

### Development spawn test

`tools/probe_travel_horse.py` is a separate diagnostic restricted to the KCD2
modding-tools executable and `kcd1_world_v43`. It spawns a native Horse with the
native Pebbles soul preset beside the development player. Repeated calls reuse
the same entity. `--own` optionally assigns this test horse to the development
player; it is not the retail transfer implementation.

Observed on 9 October: the probe spawned with both horse and actor components,
was mounted and ridden from `(3834.58,1787.66,127.287)` to
`(3489.97,1452.9,127.295)`, and ownership assignment returned the same entity ID
as the spawned horse. The development level logs a missing
`Function_switch_animal_horseBrain` and missing `playerHorseProxy` when assigning
ownership. Thus a physical, rideable horse works on the imported map, while
autonomous calling still needs separate validation. These findings belong to the
older development level, not proof of the same errors in the retail v7 package.
Runtime excerpts are retained in `outputs/horse-spawn-probe/runtime-evidence.log`.

### Correction to the v9 investigation

The user corrected the binding: **X** calls the horse, and it works after returning
to Trosky. Earlier tests using H did not test horse calling. They therefore do not
establish a broken return attachment. The v9 Player-script hook only reported
`outside_travel_maps`; its TagPoint lookup never established the destination or
the horse's state. Its guarded reattachment was not verified in retail.

The entry package no longer overrides `Scripts/Entities/actor/player.lua` or
calls `SetPlayerHorse`. The old experimental helper remains in the repository
for reference, but the builder does not ship it.

### Native destination entry (v11)

The installed native regions have world initialization that the static imported
level omitted. `tools/region_travel_entry.py` now packages the following together:

- A `LevelHolder` with the registered destination ID, a matching Skald `Level`
  definition, and its `ConceptModules` registration.
- `OnWake` and `OnLevelSwitched` feed the native streaming helper. A ready flag
  resets on entry and becomes true only after its `onloaded` acknowledgement.
  The cart interaction is gated by this ready flag. This also covers reloads and
  subsequent visits without a timer or per-frame script.
- A `GameProfileManager` arrival profile, its layer metadata, entity ID
  reservations and layer payload. The existing cart, return driver, home and
  interaction move into this profile with their identities preserved.
- `UseLayersActivation` is enabled in the mission environment, matching native
  Trosecko. The static probe had explicitly disabled it. This is the engine's
  per-level gate for [layer streaming](https://www.cryengine.com/docs/static/engines/cryengine-3/categories/1114113/pages/1048824);
  the remaining environment settings are preserved.
- Native `sa_land` world AI services, with a new compiled area covering the
  destination terrain bounds and a matching `SmartAreaManager` registration.
- Native player scheduling objects and the compiled `playerWait` and
  `animationAction` links. Trosecko quest-specific links are excluded. The
  previously imported horse scheduling objects remain present.

The streaming helper is copied from the installed KDC2 scripts; its SHA-256 and
the generated registrations are recorded under `stations.kcd1.entry` in the
build receipt. The same native `PassLongTime` pre-switch operation is now used
for both the driver dialogue and cart interaction. Existing route duration and
arrival coordinates are retained.

This implements the native **level-entry and profile-loading lifecycle**. It
does not transplant the moving arrival-cart cutscene or its road, passengers and
quest-specific conditions from Trosecko. It also does not create a substitute
horse, reset the existing horse's inventory, or run New Game.

Package checks and 16 focused travel tests pass. This is not proof that the
original owned horse now streams or answers X in retail: that requires a fresh
normal trip, followed by mounting, inventory comparison and a return trip. The
runtime flags remain false. KCD1 quest-dependent navigation profile selection
also remains unverified; copying mesh files alone does not activate those states.

## Explicit nearby horse placement (v12)

Retail diagnostics confirmed the original owned horse has a live entity after
KDC1 travel, although it is not visible/callable. The arrival hook now resolves
that same WUID and places that entity about three metres from the grounded player,
unhiding it. It never calls SetPlayerHorse or creates a replacement soul. Native
Player.lua remains intact, with OnInit/OnLoadAI wrappers appended by the builder.

The hook is limited to the KDC1 arrival marker, waits for three stable player
position samples, retries for at most 30 checks and places the horse once per
lifecycle invocation. Nearby terrain samples reject drops over 1.5 metres; this
is not a collision or navigation clearance guarantee. It logs GLUE_HORSE_ARRIVAL
before/after coordinates, identity and visibility, plus a follow-up two seconds
later. Missing entities/timeouts are logged rather than inventing a horse.

Eight executable Lua tests and 16 travel tests pass. Retail visibility,
mounting, X calling, and saddle inventory preservation still require an actual
trip. This is an explicit arrival workaround, not proof of repaired native
horse navigation. The temporary zz_horse_trace.pak diagnostic is superseded.

## Marker lookup failure and resident return station (v13)

The retail v12 run reached kcd1_travel but logged only
`GLUE_HORSE_ARRIVAL status=outside_kcd1 attempt=1`. No placement or coordinate
logging ran. The marker exists in the PAK as a TagPoint at
3831.5762,1787.6631,127.425538, matching the native arrival-marker class, but that
run's Lua lookup did not find it. This does not establish that the horse was
under terrain, or that native travel could not resolve the marker.

Map detection now reads `sv_map`, normalizes the mounted path, and allows a
bounded wait during transition. The first available player snapshot logs the
map, owned horse entity, both positions and terrain heights, visibility, and
marker/return-driver lookup results before any placement guard. A missing
TagPoint Lua entity no longer prevents placement.

The user also reported losing return-coachman interaction. The recent streaming
change is reverted for the small return station: the coachman, scheduler/home,
cart and interaction remain resident in objects_mission0.xml. Cart activation
uses its original available state instead of waiting for a profile-ready flag.
The resident station entities, return dialogue, Storm rule, soul and role were
compared byte-for-byte with v9. Level identity, native player/horse schedulers,
world AI area and navigation are retained. Optional station streaming remains
available in register_entry for investigation, but is disabled in normal builds.

All 28 focused tests pass, including missing runtime marker, mounted map-path
normalization, delayed level identity and resident return-route coverage. These
checks do not establish retail success: horse visibility/mounting and restored
coachman dialogue still need a fresh trip. The v12 failure log and pre-install
package are retained under outputs/region-travel-v13.

## Road placement and coachman registration (v14/v15; alpha.2 release)

Direct cart Use triggers were removed to avoid competing with nearby mount and
NPC interactions. Return travel is intended to use the coachman's native Talk
menu. The return dialogue now declares `NonSpeakerRoles`, following an installed
native menu-only dialogue; editor `SelectedSouls` metadata alone did not provide
that runtime participant declaration. This registration change is packaged, but
the corrected Talk menu has not been visually confirmed. Its extra diagnostic
currently catches a Lua inspection error; this does not prove dialogue readiness.

The importer now decodes nearby road edge pairs and their rendered triangles,
selects a centre position with a 3 by 1.4 metre footprint covered by the mesh,
rejects excessive slope, and leaves clearance from the coachman and cart.
Generated arrival positions are embedded in the bounded horse hook. No arbitrary
radial/off-road fallback is used. Visible horses already in the world are left
at their saved positions. Existing native Player methods and the original owned
horse are retained; the hook does not create a replacement or change ownership.

Retail v15 logs confirmed placement at `(3826.92,1783.93,127.60)`, settling to
`z=127.35`, with the same entity and owner and visibility retained. Diagnostics
identify a real `Horse`, `bIsDummy=false`, health 100 and an AI object. This
rules out an inventory mannequin for that observed entity; it does not establish
working AI scheduling, X calling, or repeated travel. A subsequent Trosky load
restored ownership after an initial zero WUID, so the first zero sample alone
must not be treated as permanent ownership loss.

The v15 installation was hash-verified against all nine generated package files.
Live visual testing was interrupted by the user. X calling, the return Talk
prompt, and repeated round trips remain explicit acceptance items.

## Building from the Windows setup release

In alpha.2, **Build region travel** reads the installed static world at
`<KCD2>/Mods/kingdomcomegluemapper/Data/Levels/kcd1_rataje` and creates a separate
`region-travel/gluemappertravel` folder in the setup job directory. Build/install
the base world first if it is absent. A world containing campaign initialization
is rejected rather than silently stripping its quests. Source CLI builds above
can select another static converted world.

This option builds only; it does not replace installed mods. Close the game,
back up an existing `Mods/gluemappertravel` folder, and copy the generated folder
into `<KCD2>/Mods`. Keep `kingdomcomegluemapper` installed for its converted assets.
If `Mods/mod_order.txt` exists, retain its entries and include both mod IDs.
Restart retail KCD2 and use an existing Trosky character and the coachman route.
Do not select Play KDC1/New Game for this travel test. Save backups are advised
for this experimental release. No generated game assets are distributed.

## Rattay table-loading regression (local v19)

The new upper-gate innkeeper package originally failed retail startup. The
official modding tools' `GeneratedDatabase.dll` reader reproduced two errors:

- Stock `PresetItem` entries had GUIDs but lacked the required native item `Name`.
- The generated outfit `Armor` entry lacked eleven required fields, including
  defence, noise, visibility, social class and `IconId`.

Stock entries now carry the verified native item name and GUID. The outfit uses
a complete native coat record with its own imported identity and clothing
component; its inherited combat/appearance statistics are native coat values,
not a conversion of the combined KCD1 garments' statistics. All table additions
are collected into one `__gluemappertravel` patch per table, retaining both coach
and merchant records. Renaming patches alone did not fix the startup failure.

`region_travel_tables.py` rejects duplicate registration IDs and these missing
required attributes before packaging. For a deeper offline serialization check
against locally installed official tools, extract the package's `Libs/Tables`
XML files and run:

```powershell
./tools/validate_database_tables.ps1 -ModToolsPath "D:/SteamLibrary/steamapps/common/KCD2Mod" -TablesPath "<extracted tables>"
```

All nine corrected table files passed that reader; 260 unit tests passed. The
user's subsequent retail launch passed the previous fatal startup stage and
began loading the existing Trosky save. This does not yet verify the new tavern
quest, merchant interaction, trading or map UI in-game.

### Map UI follow-up reported after the startup fix

The user confirmed that the KCD1 map artwork now displays in retail. Their
screenshot still shows Trosky's fast-travel and location markers over that
artwork, and they report that the other levels are missing. This is an open
issue, not a completed map conversion. The current importer registers the
background tiles and map bounds only; it does not yet import KCD1's location,
POI or fast-travel registrations. Follow-up must check the map's active-level
selection and marker ownership, import the corresponding KCD1 registrations,
and verify that the existing KCD2 regional maps remain accessible. Do not erase
the player's Trosky discoveries to hide these incorrect markers.

A second retail screenshot confirms that `A visit to Rattay` and its
`Visit the Rattay tavern.` objective have started, but the journal shows
`Different region` while the player is in KCD1. Quest activation is therefore
visually verified; association with the destination region is not correct.
Audit the quest's native level/region metadata alongside the map registration.
The screenshots alone do not establish that those two symptoms have one cause.

### Region registration repair (local v20)

`region_travel_locations.py` now imports the source world's `RPGLocationManager`
instead of registering only its map painting. The build contains 52 location
rows bound to level 1001, 1,150 source discovery records, 48 location areas,
14 town labels and 15 detailed local-map overlays with their mip textures.
Location-area WUID references are resolved through the source AI registry and
converted to KCD2 persistent GUIDs; the original polygons and world transforms
are retained. Only location geometry is imported, not its old NPC behavior links.

Map-label/local-map coordinates are converted from KCD1 world coordinates to
the target atlas's 8,192-pixel image coordinates. The old inverted height and
two-focus activation ellipse are decoded into native extents. Alternate inset
maps remain registered; overlapping enabled maps for the same location select
the smaller authored footprint. Source-disabled insets remain disabled.

POI icons reuse native definitions by GUID/label, translating changed explicit
enums (fast travel is 5 in KCD1 and 7 in KCD2). Eighteen unsupported icon types
use the native general-interest icon while retaining source labels. Ninety
quest/layer-gated marks keep their source gate; this does not activate missing
source quests. Two stale source area memberships reference deleted location
rows and are excluded with their IDs recorded in the build receipt. The
permanent fast-travel network's layer dependency is removed; route operation
and discovery transitions still need a retail test.

The Rattay journal quest is now defined inside the `kcd1_travel` Level, matching
the native hierarchy. The original root module retains its `progress` and
`objective` State nodes and sends their values into the regional journal, so
their saved node paths are not intentionally moved. The root module explicitly
keeps the old Quest's Haste namespace; the new journal does not declare another
namespace with the same name. Save migration and the displayed region still
require live confirmation.

Offline checks: 263 tests pass; all 13 generated table XML files pass the
official `GeneratedDatabase.dll` reader. The full builder succeeded. Comparison
against installed v19 showed no Trosky level changes, no changes to existing
dialogue/quest strings, and only formatting differences in the previously
repaired inventory/outfit table rows. All imported location references resolve
to level 1001. The first v20 retail test has loaded a KCD1 save and opened both
map and journal without another startup-table failure; this does not by itself
prove that markers, region text or town overlays are visually correct.

The subsequent user test confirms that map items now appear and work, including
the multiple map-detail levels. Remaining reported issues are the Rattay
innkeeper's missing Talk interaction, facing/leaning placement, and the quest's
missing map marker. The requested completion condition is conversation with the
innkeeper, not entering the inn polygon. These are separate follow-up work.

### Innkeeper interaction follow-up (local v22, awaiting retail confirmation)

The visit state now accepts a `talked` input connected to the innkeeper
FaderDialog's native `BeforePlay` output. An Active-state guard allows completion
only while the quest is in progress. The old area trigger, its graph asset and
its imported polygon are removed. Entering the inn cannot complete the quest.
The objective reads `Talk to the Rattay innkeeper.` and its active journal log
binds `Marker` to a SoulAsset containing the original innkeeper's soul GUID.
The persistent state names and the Level-owned journal are retained.

The dialogue uses a General-priority service menu, an explicit source-person
selection, and the non-speaking innkeeper role. Trade remains a native OpenShop
sequence with the merchant as its response role. This adds no invented voiced
conversation. The first genuine dialogue start is the completion event; buying
an item is not required.

The old registration had no compiled person activity. The importer now follows
the source NPC's work-area Lean links, selects the point on the closest floor,
then the closest point on that floor. For this person that selects the outside
right-lean point at `2891.9773,730.18958,107.4`, with the original quaternion
`0.84804809,0,0,-0.52991927`. Neither coordinate is a constant in the importer.
The selected point becomes a native DetailMovementSmartObject. Two compiled
records connect person -> SchedulerHub -> native `use` leaning activity, and
the corresponding entity/waiting links are exported. The shipped
`AI/world/so_leaning_right.xml` enables conversation contexts while leaning;
this replaces an unscheduled idle actor. Existing saves may retain an actor's
current pose, so the activity must also be checked after loading an existing
visit, not just on a first arrival.

Person import is shared through `character_person.py` (source identity, soul,
Storm and world registration), `character_person_appearance.py` (male/female
appearance and fixed outfit) and `character_person_activity.py` (authored lean
point conversion). The merchant adapter adds its shop and dialogue. Callers
choose source person, namespace, native brain and role; the generic helper
does not select Rattay or a quest. The old merchant appearance import remains
a compatibility alias. Existing soul, instance, outfit and asset identities
are retained.

The fixed outfit now keeps source arms/hands underneath sleeves. Previously
the male arm clothing element set KeepBodyLayer=false, hiding bare forearms
along with covered upper arms. Geometry and materials are unchanged. A real
female import (`aus_bartender`, staged only, not installed) also exposed a small
uniform internal/render position offset in a source garment. The helper can
normalize a uniform offset under 1 cm in the internal buffer while preserving
every render position. Nonuniform/large discrepancies still fail; this does not
relax the existing skin upgrader's consistency checks.

Validation: 270 tests pass. All 13 global and six destination-level table files
pass the official GeneratedDatabase reader, including scheduler.xml. The female
helper build's four tables also pass. Compared with installed v20, six common
archive entries change: four quest/dialogue graphs, CharacterComponent and the
mirrored destination scheduler. Trosky level, KCD1 terrain and navigation archive
contents are unchanged. The level scheduler matches its common-archive mirror.
The generated activity references resolve to registered entities.

Installed common PAK SHA256:
`5D341BE4F86C6F11F2F18F35B614D137CC993D622BE93118B1ABC30EF779FBAA`.
The game was closed for installation. The common PAK, destination level PAK and
English localization were backed up, replaced and hash-verified; no save files
were edited. Local receipts are in `outputs/region-travel-v22/installed-v22.json`
and `validation-summary.json`.

Retail acceptance is still pending: approaching the inn must leave the quest
active; its marker should follow the innkeeper; Talk must open his menu and
complete the quest; Trade must open the original stock; arms should remain
visible up close; and the actor should use the authored outside lean. Source
daily scheduling and lodging remain outside this interaction fix.

### v22 retail entry crash and v23 scheduler repair

The user's next retail test crashed while entering KCD1. The crash dump records
a null read at address 0x20 in WHGame.dll (RVA 0x644f5d). Unwinding the exception
stack and matching PE RTTI identifies C_SchedulerSubbrain and C_AIBrainMultiSubb
in the calling chain. Evidence is retained locally under
`outputs/innkeeper-crash-v22/`.

The imported leaning target existed in objects_mission0.xml, but its compiled
scheduler record was missing. Native leaning objects have an empty C_SmartHub
record even when they have no outgoing activity links. The earlier two-record
NPC -> hub -> lean export therefore left the terminal unresolved inside the
scheduler. XML deserialization and entity-reference checks did not detect this.

The importer now copies and remaps that native terminal record, exporting three
records for the person/activity. It rejects missing or duplicate compiled
scheduler identities and checks every activity destination against scheduler
records. The regression test removes the leaf while retaining the entity and
requires validation to fail.

The full v23 build and all 270 tests pass. Its seven destination scheduler
records have no unresolved activity targets, and the table passes the official
reader. An archive comparison against v22 confirms that only scheduler.xml and
its common-archive mirror change. Both packages were backed up and replaced,
with installed hashes recorded in `outputs/region-travel-v23/installed-v23.json`.
Retail entry, Talk and quest completion still require a new user test; the
offline results do not establish that the crash is resolved in-game.

The subsequent user test reached KCD1 successfully and reports that the
remaining innkeeper issue is the missing Talk option: only Rob appears. The
retail log also records destination arrival, horse placement and a normal
ExitGame shutdown. This confirms entry after the scheduler repair, but does
not confirm dialogue, trading or conversation-based quest completion.
