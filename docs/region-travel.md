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

### Navigation mount and passive retail trace (v35)

The destination's exported `AI/LevelPath` is `data/levels/kcd1_travel`,
while the retail log mounts its level/recast archives under
`mods/gluemappertravel/data/levels/kcd1_travel`. The builder now also exposes
the original navigation graph, areas, mesh indices, tiles and state groups at
the declared AI path through the root mod PAK. These are byte-identical aliases,
not a new mesh or a change to horse ownership. All 738 navigation resources
are present at the additional address. Whether this resolves autonomous
movement still requires a retail test; matching binary versions alone does not
prove usable navigation or quest-dependent state selection.

`--trace-ai` adds an optional passive trace to the installed native horse-command,
animal interrupt-host, horse movement, human interrupt-host and hit/flee paths.
Look for `GLUE_AI_TRACE` in `kcd.log`. `horse_command:begin` shows receipt of the
native ComeToMe command; `horse_host:success` shows that its service search
succeeded; `horse_move:begin` and `horse_move:failure/success` distinguish movement
execution from command delivery. A missing phase alone does not prove its cause.
The trace runs only in the imported map, caps each actor/phase at eight entries
and the session at 400 events, and makes no input, ownership or movement calls.
It is disabled in ordinary builds. The original conditions, actions and return
statuses are preserved. Retail behavior remains unverified until the user tests
X and NPC reactions in this build.

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

### Innkeeper Talk participant repair (local v24)

The native BasicAIActions:ActorCanTalk calls actor:CanTalk(player.id) before
adding the Talk action. The generated innkeeper dialogue contained only a
merchant Response. Henry appeared in SelectedSouls, which is authoring metadata,
but had no actual response/participant in the dialogue. The working return
driver includes a Henry Response, and the native male merchant dialogue uses a
player Shop topic followed by a merchant OpenShop action.

The innkeeper now uses that structure: the visible Trade sequence is Type=Shop
with a Henry Response and EndType=Decision, followed by an autoselected native
OpenShop sequence with the merchant Response. This preserves the native Talk
and shop path instead of overriding interaction predicates. The existing
BeforePlay connection was intended to complete the visit quest when conversation
starts; the subsequent retail test below showed that it did not.

All 270 tests pass; the regression checks both runtime participant roles and
the nested shop action. Full rebuild comparison shows exactly one changed
common-archive resource, `Quests/GlueTravel/rattay_innkeeper.xml`. All other
common resources, both level archives' contents and localization match the
installed v23 build. Only the common PAK was replaced, after backing it up and
verifying its installed hash. Receipts are in `outputs/region-travel-v24/`.
Talk availability, quest completion and trading still need retail confirmation.

### Talk confirmed; conversation-entry and shop-action repair (local v25)

The user confirmed that v24 exposes Talk and opens the innkeeper dialogue, but
selecting Trade does not open the shop and entering the conversation does not
complete the quest. Those two behaviors were not established by the earlier
static participant checks.

The adapter now follows the shipped KCD2 travelling-merchant dialogue at
`Quests/Final/Barbora/random_events/pocestny/event_pocestny/pocestny/traveling_merchant/traveling_merchant_man/obchodnik_na_ceste_muz.xml`:
an autoselected, silent Henry entry sequence emits an explicit `dialog_started`
output before entering the services decision. That event feeds the existing
persistent quest's `talked` input. The Active guard and saved state paths remain
unchanged, so walking into the area, buying an item or repeatedly speaking to an
already completed quest cannot substitute for or restart the objective.

Trade is now the visible `OpenShop` sequence with a Henry response, as in that
same native dialogue. It no longer depends on a cosmetic `Shop` topic followed
by an autoselected child action. The merchant remains a non-speaking participant.
Shop owner/keeper/storage links were compared with native Procek and retain
their native directions; no database, stock, character or scheduler changes
are part of this repair.

Event-only `GLUE_RATTAY_SHOP` lines record `talk_started` and `trade_selected`,
including the native shop ID resolved from the actual dialogue participant
(expected 20094). Queries are protected so logging cannot abort the action.
Graph traces `talk_received` and `quest_completed` record the quest progress at
those events. There is no timer or per-frame polling.

All 271 tests pass, including automatic entry before topic selection, the
explicit completion event, the native OpenShop choice, and the Active-only
completion guard. Retail acceptance still requires opening Talk without buying
anything and observing the completed quest, then selecting Trade and observing
the actual shop inventory. Offline checks are not that runtime proof.

The v25 rebuild changes only three common-archive graph resources: the project
connection, the innkeeper dialogue and the visit state module. All level PAKs,
tables, localization and character resources match v24. The common PAK was
installed with the game closed after backing up v24 and verifying both hashes.
Evidence and the installation receipt are under `outputs/region-travel-v25/`.

The subsequent retail test confirms that opening Talk completes the quest.
However, the conversation closes without displaying a Trade choice. The retail
log records both `talk_started` and `trade_selected` four times, each with
`shop=-1 query_ok=true`. Thus the action is executing, but the native shop
lookup cannot resolve a shop for the dialogue participant. Shop registration
and the menu behavior remain unresolved at this checkpoint.

### Staffed shop activity and explicit services menu (local v26)

The v25 log proves the silent conversation entry and shop action both execute,
but the native keeper lookup returns -1. Comparing the native tavern scheduler
reveals that owner/shopKeeper links are only part of merchant registration:
its work hub also has `ElementInitializerOpenShop` and
`ElementInitializerAddContext Context="shop_sellerReadyToSell"` for the
innkeeper activity role. The previous imported leaning hub had neither.

The reusable `character_person_shop` adapter now copies these two initializers
and the empty shop terminal from the installed native scheduler, remaps the
shop target and supplies the occupation role on the person's incoming work
activity. The existing lean activity and other scheduler records remain intact.
Compiled activity/effect links live in scheduler.xml; waitinglinks.xml retains
the runtime owner, keeper and storage relationships. Both scheduler copies in
the level/common packages are kept identical.

Farewell is enabled again. The former single-action services decision logged
trade_selected immediately after Talk rather than showing a choice. Enabling
the ordinary End dialog alternative keeps the intended Trade/End dialog menu.
The confirmed conversation-entry quest event and persistent state are unchanged.

All 272 tests pass. The eight-record scheduler passes target validation and the
official GeneratedDatabase.dll reader. The archive comparison limits changes
to the innkeeper dialogue, entity/link metadata and scheduler plus its mirror.
The v25 retail log and v26 validation/build receipts are retained under
`outputs/region-travel-v26/`. Opening the shop and its stock still need retail
confirmation; the next event log should resolve shop 20094 rather than -1.

With retail closed, both changed packages were backed up and replaced. Their
installed hashes match the v26 build (`installed-v26.json`). The pre-install
level PAK had older ZIP metadata but byte-identical entries to the v25 rebuild;
entry hashes were checked before replacement. The v25 quest-completion checkpoint
is committed and pushed as `0e7d211`; v26 remains a local retail-test candidate.

### Explicit topic menu and live shop-owner binding (local v28)

The next user test confirms that v26 still exits on both the first and later
Talks. Quest completion works. The retail log again pairs talk_started with
trade_selected and reports shop=-1, so enabling AllowFarewell did not restore
the menu and the scheduler additions did not resolve the keeper lookup.

The dialogue now has an explicitly non-autoselected services decision with two
real player topics: Trade (Type=Shop) and End dialog. OpenShop is a merchant
action nested exclusively under Trade, following the native shop dialogue
separation between a selectable topic and a terminal action. The silent entry
still emits dialog_started and continues to services. Only choosing End dialog
or the shop action reaches EndDialogue. Automatic farewell is disabled to avoid
an additional implicit option; the explicit exit uses native ui_end_topic.

The reusable shop adapter also follows the shipped
`random_events/events_common/spawned_shop.xml` contract. A Level-owned state is
activated by OnWake/OnLevelSwitched, feeding SetOwner and SetEntityContext.
The ShopAsset is bound through an asset link on the destination LevelHolder;
the SoulAsset resolves the imported person's original shared soul identity.
The placed shop has bOwnerIsSpawned=1 as in native event-merchant shops. These
bindings do not depend on the visit quest and do not reset stock or inventory.

Event-only diagnostics distinguish talk_started, trade_selected, open_shop and
leave_selected, and report the keeper name, shop entity presence, stock-linked
shop ID and seller context. Shop correctness is still pending retail evidence:
Talk must leave a menu open, End dialog must leave without attempting to trade,
and only selecting Trade should log the handoff and open the shop.

All 273 tests pass. Earlier v27/v27b build folders are intermediate outputs and
were not installed; v28 is the final test candidate for this change.

With retail closed, the installed v26 package hashes were checked, both changed
PAKs were backed up, and v28 was installed with matching source/destination
hashes. The comparison against v26 contains only two dialogue/level graphs and
the destination entity/link metadata. Scheduler, visit quest, localization,
terrain, navigation and Trosky resources are unchanged. The install receipt and
validation summary are under `outputs/region-travel-v28/`; retail menu and shop
acceptance remain pending.

The subsequent retail test confirms that Talk now leaves the menu open and
completes the visit quest. Trade is visible, but selecting it closes without a
shop. Logs distinguish the selected Trade and OpenShop actions from End dialog.
They report `shop=-1`, `shop_entity=true`, `stock_shop=20094`, and `ready=true`:
the stock resolves to the registered shop and the seller context exists, but
the live keeper-to-shop association is still missing. This checkpoint preserves
the working menu and quest completion; shop opening remains unresolved.

### Owner-inventory shop storage (local v30)

The confirmed v28 menu/quest checkpoint is pushed as `eadc984`. Its retail log
resolves the shop through the stock stash (20094) and reports ready=true, but
keeper lookup still returns -1. The native ShopModule distinguishes a scheduled
OpenShop NPC element from ownership or seller context. Its owner-only lookup
fallback is unavailable when goods are assigned to stashes or spawned display
items. The shipped module explicitly diagnoses these three separate conditions.

The imported merchant now uses owner-inventory storage, matching the native
spawned-shop ownership contract already installed. The exporter no longer
creates or links the extra shop stash. The original imported shop inventory
preset, 22 item types and quantities are unchanged; no items are manually
granted and no saves are edited. The existing activity can still register an
active keeper when it runs, but an owner-inventory shop can also use the native
owner fallback. This does not claim that the complete native daily schedule
has been imported.

Dialogue diagnostics now include the resolved owner's name and count of live
shopStash links. The expected retail evidence is shop=20094, owner=rato_innkeeper1,
stash_links=0, followed by the native Trade screen. A loaded older map state may
retain its old stash relationship; the live count distinguishes that case.
The confirmed topic menu and quest event remain unchanged. Retail acceptance
of opening the shop, stock contents and a transaction is still pending.
The v29 folder is an intermediate build; v30 includes the final diagnostics.
The v30 packages were installed with retail closed after verifying and backing
up both v28 PAKs. Installed hashes match the build. Archive contents differ only
in the diagnostic dialogue script and destination entity/link metadata; all
tables, the 22-type stock preset, quest state/journal, localization, scheduler
and terrain/navigation resources are unchanged. Receipts and the previous
retail log are under `outputs/region-travel-v30/`.

The subsequent user retail test confirms that Trade now opens the shop and a
cheese purchase succeeds. Together with the preceding test, the confirmed flow
is Talk -> quest completion with the menu retained -> Trade -> purchase.
This validates basic buying; it does not verify every stock entry, selling,
restocking or save/load behavior. Haggling is not implemented yet and remains
a separate outstanding part of the merchant integration.
## Whole-world NPC dependencies and service registration

The importer now reads all human placements from the source mission and layers,
and joins their SoulList records by persistent entity GUID. The installed KCD1
export has 2,387 human placements (952 resident and 1,435 conditional). Shared
names are not identities. Source instance overrides and inline souls without a
SharedSoulGuid are retained, including overnight schedules written as 24:xx.

`tools/character_world_dependencies.py` discovers the transitive entity graph and
typed soul, faction, superfaction, brain, class, inventory, clothing, weapon and
appearance dependencies. It retains inventory selection rules rather than rolling
them during conversion. Entity allocation and graph binding are separate phases,
so owner/home cycles do not cause duplicate creation. An unconverted dependency
marks every dependent NPC incomplete. Each travel build writes the source graph
to `world-npc-dependencies.json`; this is not evidence of full native conversion.

Two source mapping errors were corrected: `faction.superfaction_id` refers to the
separate superfaction table, not another faction; and KCD1 soldier social classes
with crime role 2 map to KCD2 `soldier_crimeAuthority`, not its non-authority
`soldier` class. Imported civilian settlements extend the native civilians tree
while retaining its full existing contents. The native Storm guard selector adds
the imported region to its region gate while keeping the social-class gate.

`tools/character_world_services.py` adds the resident source parking points (57),
five level exits, 89 hangover points and their hub, eight town-refuge destinations,
the native flutist controller and player-link router. Both EntityLinks and the
compiled waiting-link network are written. Horse parking includes the native
horse-drink animation-helper collection. Emergency destinations currently use
source town shelter anchors; retail movement to them is unverified. Conditional
parking camps are not made permanent, and native quest horse overrides are not
assigned to the player's horse.

Full population/system conversion remains incomplete. Native punishment scenes,
their actors, the open-world crime concept module, guard bundles, source daily
activities, non-daycycle brains and quest-controlled layer activation still need
creation adapters. The build receipt explicitly reports these dependencies and
does not mark all services registered. Do not describe the source census, serializer
checks, or a successfully spawned model as proof of working crime, fleeing,
horse summoning, or a complete population. No retail behavior is verified by this
offline conversion pass.

## Resident population upgrader

The `codex/kcd1-population-import` branch adds a resumable bulk conversion using
the same person importer as the merchant and side quest. The October 10 source
run converted all **950 additional resident humans**, preserving the two people
already present in the base package. This is **952 source resident placements**
with no resident conversion failures. It is not a claim that the population has
been observed in retail or that every AI system has been converted.

```powershell
.venv/Scripts/python.exe tools/character_population.py `
  --source 'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance' `
  --target 'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2' `
  --base-package outputs/BuildCache/TravelBase/gluemappertravel `
  --output outputs/BuildCache/Population

.venv/Scripts/python.exe tools/package_character_population.py `
  --stage outputs/BuildCache/Population `
  --output '<fresh-folder>/gluemappertravel'
```

The input base must be a built travel package with the native services, map and
existing quest characters. The output is a separate package; these commands do
not install it or operate the game. Cached conversions are keyed by source
placement and instance data. Use a fresh stage after changing conversion policy;
an interrupted run with unchanged policy can resume without rebuilding finished
people. DDS families are compressed and shared across appearances while person
records, meshes and material assignments remain independently namespaced.

The upgrader now handles omitted source bodies, case-insensitive morph names,
fixed exports with stale variant references, retired authoring suffixes whose
exact base asset still exists, unnamed helpers, empty outfits, inventory-only
jewelry, and bespoke female dresses classified from source armor metadata.
Missing morph channels preserve the exported mesh rather than inventing a shape;
the receipt records the unavailable channel. Different source skin exports can
have uniform internal/render offsets, which are normalized without changing the
rendered surface. Skeleton palette merging takes a joint's bind pose from a
garment that actually weights it, ignoring stale unused copies in other garments.

Every source faction/superfaction and its directed relationships is created, with
missing location dependencies added. Instance souls receive distinct stable IDs
so shared source souls cannot overwrite each other's overrides. Homes use source
persistent IDs and compiled float32 geometry; repeated work/ownership links to
the same polygon are deduplicated. Existing native services and shop activities
are retained.

`npc_dummyWait` and `npc_test_base` use the native idle-only `npc_default` brain.
`npc_invisible` is upgraded from its source behavior tree with native expression
syntax, mailbox registration and existing native buff dependencies. Empty native
appearance components preserve its renderless purpose in place of the removed
source `LODLock`. This helper conversion still needs a runtime LOD/save-load test.
Helpers without a source faction stay unaffiliated and use the native `none`
social class rather than being assigned to a town.

The 1,435 conditional human placements are inventoried separately and are **not
yet converted by this resident pass**. Their source layer activation, battle and
pursuit controllers, quest contexts and dependencies remain further conversion
work; they must not all be activated as permanent residents. Ordinary residents
currently have an interruptible native scheduler baseline with source homes.
Full source daily activities, non-outfit inventory/weapons, voices/dialogues,
crime dependencies and gameplay behavior still require their respective adapters
and live tests. `population.json` and `population-package.json` keep
`all_people_loaded`, `runtime_verified` and `systems_verified` false until those
acceptance boundaries have actually been tested.

## Current build and storage

The complete current mod set lives at `outputs/CurrentBuild/Mods`: both
`kingdomcomegluemapper` (converted world/assets) and `gluemappertravel` (travel,
map, quests, merchant and population). This is a build directory, not a second
game installation or a mounted C: directory. `build.json` records file hashes
and the validation results. A built package does not imply it is installed.

`tools/current_build.py` uses this layout relative to the repository:

| Path | Purpose | Retention |
| --- | --- | --- |
| `outputs/CurrentBuild` | One complete checked build | Replaced after the next build passes validation |
| `outputs/BuildCache/World` | Converted base world | Rebuildable with the existing setup converter |
| `outputs/BuildCache/TravelBase` | Generated travel overlay before bulk population | Rebuilt with `--refresh-base` |
| `outputs/BuildCache/Population` | Resumable person archives and shared DDS families | Rebuilt with `--refresh-population` |
| `outputs/BuildCache/build-inputs.json` | Local source/target/mod-tools paths | Keep this small build configuration |
| `outputs/BuildWork` | Candidate package, table extraction and temporary files | Generated scratch; do not clear during a build |
| `outputs/BuildLogs` | Latest native table validation log | Small diagnostic evidence |
| `backups/CurrentBuild/<UTC timestamp>` | Files changed or added manually to the previous build | Preserve; unchanged generated assets are never copied here |

```powershell
.venv/Scripts/python.exe tools/current_build.py status
.venv/Scripts/python.exe tools/current_build.py build
.venv/Scripts/python.exe tools/current_build.py validate
# After changes to the travel generator or appearance conversion policy:
.venv/Scripts/python.exe tools/current_build.py build --refresh-base --refresh-population
```

The local `build-inputs.json` contains `source`, `target`, `mod_tools`, and
optionally `powershell` (the PowerShell 7 executable). The base world cache is
seeded from the already converted world mod, not the original game PAKs; to
recreate it from scratch, run the setup world's conversion/build workflow and
place the resulting `kingdomcomegluemapper` package in `BuildCache/World`.
Current-build assembly does not yet invoke that terrain pipeline itself.

All Python and child-process temporary output is redirected into `BuildWork/temp`
on the workspace drive. A working lock rejects concurrent builds. A candidate
must pass world/texture/identity validation and the official GeneratedDatabase
serializer before promotion. Failed validation leaves the current build intact.
The previous generated build exists only during directory promotion for rename
failure recovery and is then removed; it is not retained as an asset backup.
If a process dies during promotion, inspect `BuildWork/previous` before retrying.
Source changes belong in Git on the population branch. Saves and user-authored
content are not managed or deleted by this build tool.

### Conditional population conversion

The population stage now visits conditional placements as well as residents.
Appearance archives are independent of brain registration: an actor whose old
combat subbrain still needs an adapter can retain its converted appearance
without being counted as a registered NPC. KCD1 combat subbrain type 2 does not
exist in the KCD2 database; copying that row or assigning a civilian brain is
not a behavior conversion.

The packager currently converts complete actor-only layers. It checks both the
Objects XML and the compiled terrain object layers; an XML file containing only
NPCs is not sufficient evidence if the same layer owns brushes or vegetation.
Mixed entity layers remain dependencies of their source profiles. Converted
actors, idle delegates and private home hubs remain in the same native layer.
Shared home polygons, persistent SoulList records and compiled scheduler records
are registered separately. Unloaded-layer entity IDs are reserved before new
resident IDs are allocated.

`registered-profiles.json` contains the actual destination profile names for the
campaign compiler's `--registered` input. The compiler translates literal
`EnableProfile` operations to native Skald Layer ownership and remaps
`ProfileLoadedGate` to that same profile. Its State nodes retain activation state;
there is no polling script or automatic enable-all operation. Source navigation
and SVO-dependent profiles need their corresponding world conversion before
registration. The current travel pack does not yet execute all original quest
controllers, so profile registration alone does not activate those quests.

For an intermediate gameplay checkpoint while appearance staging continues:

```powershell
.venv/Scripts/python.exe tools/current_build.py build --cached-only
```

This takes a separate `population-snapshot.json` from already published archives,
leaves the live conversion checkpoint alone, and still requires every resident
to be converted. The normal world/database checks run before CurrentBuild is
replaced. Its package receipt records the exact resident and conditional
placements, so subsequent cache progress cannot change the build's validation
set. Do not combine `--cached-only` with either refresh option.

Building does not update the retail installation. Close the game/editor, then
install the validated checkpoint explicitly:

```powershell
.venv/Scripts/python.exe tools/install_current_build.py
```

For a controlled test with only the original coachman, innkeeper and Theresa:

```powershell
.venv/Scripts/python.exe tools/current_build.py build --population-profile services-only
.venv/Scripts/python.exe tools/install_current_build.py
```

This is a build setting, not a removal of the population implementation. It keeps
the existing CurrentBuild scenery, level assets, gameplay scripts and localization,
but packages only TravelBase's NPC dependencies, persistent souls, schedules and
layers. Population-only assets are omitted as well, so they cannot remain mounted
and consume resources during the comparison. The installer removes obsolete
managed shards. Player/horse and the three original services remain in the base.
Validation checks both world registrations and globally mounted resources; merely
hiding the extra entities does not pass.

The selection is saved in `BuildCache/build-inputs.json` after a successful build.
The converter and its full appearance cache remain untouched. Restore full
population generation with:

```powershell
.venv/Scripts/python.exe tools/current_build.py build --population-profile full --cached-only
.venv/Scripts/python.exe tools/install_current_build.py
```

Restart retail and travel from a Trosky save for the comparison. A save made in
Rattay with the full population can retain serialized state from those actors.
This setting isolates population load; it does not establish that scenery or
material rendering is fixed.

The installer checks every source file against `CurrentBuild/build.json`, stages
only differing files on the workspace drive, and verifies every installed file.
It refuses to replace packages while the game/editor is running and preserves
unmanaged files. Replaced generated packages are held only during installation
for rollback; they are not retained as redundant backups. An interrupted install
leaves `BuildWork/install` for inspection. The successful installation receipt is
`BuildLogs/current-build-installation.json`, including the build timestamp and
installed hashes. This proves deployment, not live NPC behavior.

Root mod PAKs use bounded ZIP32 archives (at most 1 GiB and 50,000 entries
per generated shard). Retail rejected the earlier 12.91 GB population PAK with
`Too big PAK file`, which also removed the travel dialogue because those records
were in that same unmounted archive. Validation now reads all population shards
and checks archive limits before promotion. Native exported terrain tile archives
use a separate loading path and may exceed that root-archive entry budget.
`repack_current_travel.py` can migrate a completed checkpoint without reconverting
characters. Travel builds omit the old campaign prototype's main-menu override;
the campaign prototype source remains available separately.

After the root archives mounted correctly, retail hit its 32,768 shader-resource
limit while loading Trosky. The population checkpoint contained 34,934 material
files with 59,998 shader-bearing material entries, but only 2,284 distinct XML
definitions (5,476 shader entries). `character_shared_materials.py` now shares
exactly equivalent definitions at package time. It preserves material names,
colours, textures, shader settings and submaterial order, rewrites character
component paths and embedded mesh material-name chunks, and leaves geometry and
skin weights untouched. `shared-materials.json` records the reduction. The
validator checks emitted component assets and shared material textures. These
counts measure package contents, not the engine's live resource allocation;
successful retail loading remains a separate test.

Installation removes obsolete files only when they belong to the previous
installation receipt and still match their recorded hashes. This prevents old
population shards from remaining mounted after a smaller repack. Modified or
unmanaged files are preserved; removed files participate in installation rollback.

The coach labels name the Rattay and Trosky regions. Both custom LevelSwitch rows
set the native `WorldTimeDurationInHours` to 72; the original routes keep their
own durations. Three days is a provisional historical travel estimate, not a
measured medieval itinerary. The University of East Anglia's
[Magna Carta Project](https://magnacarta.cmp.uea.ac.uk/read/magna_carta_1215/Clause_30?com=aca)
estimates 20-25 miles (32-40 km) per day for a loaded horse cart. Weather, road
conditions, stops and the chosen route would vary the actual duration.

The destination retains the native time-of-day lighting profiles and now copies
the native weather-condition table into both the level and its mounted table
alias, replacing the empty bootstrap shell. Those condition rows gate weather
effects; they are not themselves the sky's weather-selection controller. No
permanent sunny or rainy override is applied. Time advance and changing weather
still require live verification; restoring the table alone does not prove that
the reported sunny-weather symptom is resolved.

Test the resident population and existing travel services first: reactions to
drawn weapons and hits, navigation, save/reload, and a trip out of the region and
back. Conditional load/unload requires the corresponding native profile owner
to be activated. These builds still use interruptible baseline scheduling; full
source work/eat/sleep routines, battle controllers, non-outfit inventories and
voice/dialogue dependencies are not yet fully converted or runtime verified.
