# KCD1 campaign startup, quests and saves in KCD2

Status: implementation specification, 8 October 2026. No campaign bootstrap,
quest migration or save compatibility is implemented by this document.

## Intended result

Distribution target: a local setup consumes the installed retail KCD1 and KCD2
files and produces a normal KCD2 mod. Launching retail KCD2 should expose
**Play KDC1** from its menu. The modding tools and their reference database may
inform development, but must not supply the campaign or be required by setup.
Quests, scripts, dialogue and campaign state definitions come from KCD1 retail
data. See [retail setup progress](retail-setup.md) for the implemented loader probe.

Start a new KCD1 campaign inside KCD2, play Henry's opening in Skalitz, interact
with his mother and Martin, progress the original objectives, and save, exit,
continue and reload without losing or duplicating progress. Expand from that
working opening to the rest of the campaign.

Use KCD2 for the player, actors, interactions, inventory, quest presentation,
dialogue execution, navigation, audio playback and save serialization. Convert
KCD1's content and behaviour into those systems. A visual map load, idle NPC,
console-spawned character or handwritten save-position file is not this result.

The initial save target is **new saves created in KCD2 for the converted
campaign**. Loading an existing KCD1 `.whs` is a separate migration project:
binary compatibility has not been established, and is not assumed here.

## What local inspection established

These are installed-file observations, not runtime compatibility claims.
Archive paths below are relative to each game's installation unless marked
as the KCD1 modding-tools ZIP. Base archive observations must be reconciled
against patches and loose overrides before becoming conversion inputs.

| Evidence | Finding | Consequence |
|---|---|---|
| KCD1 `Data/GameData.pak: Libs/UI/UIActions/MM_NewGame.xml` | Prepares a new game, receives `OnNewGamePrepared`, sends `questModule:onNewGame` to `q_master_main`, then calls `NewGameStarted`. Also sets calendar speed and menu audio. | Starting the story has a lifecycle; teleporting Henry does not execute it. |
| KCD1 `Data/Scripts.pak: Libs/AI/quests/q_master_main.xml` | Handles the new-game message, enables `q_skalitz_normal`, resolves the linked initial quest object and starts `q_skalitz`. | Translate the relevant bootstrap and links, not the entire global script indiscriminately. |
| KCD1 `Data/GameData.pak: Libs/quests/flowgraphs/q_skalitz.xml` | 109 objective nodes, 26 tracked-asset nodes, 9 item assets, 8 NPC assets and 6 place assets. | Even the first quest has hidden states and dependencies beyond its visible errands. These are graph-node counts, not a count of independent quests. |
| KCD1 `Data/Scripts.pak: Libs/AI/quests/q_skalitz.xml` | Behaviour trees, message subscriptions, Lua calls, waits, objective gates and persisted variables including `p_phase` and `p_startOfGameDone`. | A node-name replacement cannot preserve behaviour, concurrency or save semantics. |
| KCD1 tools ZIP `Data/Levels/rataje/LayerExportProfiles/q_skalitz_normal.xml` | Explicit enabled/disabled layers, navigation and parent profiles `e3_2017_all`, `game`, `q_skalitz`. | Resolve profile inheritance and GUIDs. The visual importer's shared/initial filter is insufficient for campaign state. |
| KCD1 tools ZIP `Data/Levels/rataje/Layers/Design/_global/q_master_main.lyr` | Authored quest objects and GUID-based links to `q_skalitz` and other objects. | Rebuild persistent identity and relationships. |
| KCD1 `Data/Levels/rataje/level.pak` | Contains a 4,806,797-byte `newgame.whs`, plus `waitinglinks.xml`, `extractedlayerentityids.xml`, `triggerareas.fubar`, `whdata_0/1` and movie/mission data. The snapshot is not a ZIP container. | Treat the original initial snapshot as reference evidence; reconstruct target initialization. Internal snapshot serialization remains unexamined. |
| KCD1 tools ZIP `Data_reference/modding.sql` | 489,486,727-byte SQL dump with 468 table declarations, including quests, transitions, sequences, sequence lines, dialogue commands and Skald entities. | Extract a connected opening-content subset from structured authoring data. Do not reconstruct dialogue from audio filenames alone. |
| KCD2Mod `Data/Scripts.pak: Quests/Final/Barbora.xml` | Skald project with modules and typed state; associated quest files contain dialogue/sequence definitions. | Target native Skald definitions. KCD1 also uses Skald-related data, but the inspected runtime layouts differ. |
| KCD2Mod `Data/Scripts.pak: Quests/Testing/test_of_everything/test_of_everything/saveGameWithNotification.xml` | Native `SaveGameWithNotification`, `EnqueueSave`, `OnDone` and typed `State` examples. | There is a native save request path to investigate; this does not prove arbitrary imported state is serialized. |
| KCD1 `Localization/English.pak`, `English_xml.pak` | Individual dialogue `.ogg` files and localized UI/dialogue/quest text XML are available. | Voice and subtitles have a practical extraction route, subject to ID reconciliation. |
| Both games' `Data/Sounds.pak` | FMOD banks exist. KCD2 examples also use paired `.assets.bank` files. | Bank/event compatibility needs a bounded playback test, not a blind replacement of master banks. |

The development installation also contains copied/control levels. In
particular, a `newgame.whs` under its `rataje` copy is **not evidence of native
KCD2 snapshot compatibility**. Use retail KCD2 `trosecko`/`kutnohorsko` as
native references, and record which installation supplied each input.

Current project foundation: the world visuals, registered Martin, selected
native animation and blood/grime conversion are demonstrated. General NPC AI,
campaign navigation, quest interactions, dialogue and save restoration are
not demonstrated. Existing facial-animation warnings remain relevant.

## Proposed conversion architecture

Build a dependency manifest and a typed intermediate representation (IR), then
emit target data through separate adapters. Keep game logic in native persistent
systems where possible; reserve a small compatibility layer for missing
semantics. Do not create a second independent inventory or save engine.

```text
KCD1 effective retail packages + compiled layer/profile and database data
                          |
          dependency closure + stable identity map
                          |
           campaign / quest / dialogue / world IR
                          |
      +-------------------+----------------------+------------------+
      |                   |                      |                  |
 native Skald       native entities,       native dialogue      native audio
 quest/state        items and layers       and sequences        controls/assets
      +-------------------+----------------------+------------------+
                          |
       KCD2 campaign lifecycle and native save/load
```

Every converted record retains source archive/table/row or layer GUID, target
identity, dependencies, conversion status and diagnostic reason. Classify
operations as directly supported, translated, adapter required or blocked.
Unresolved required references must fail the build; unsupported behaviour must
never silently become a successful no-op.

Map source quest/objective IDs, soul GUIDs, placed entity GUIDs, items, dialogue
sequences, lines, audio events and layers. Reuse stable source IDs where target
semantics and collisions permit; otherwise produce deterministic namespaced
IDs and a reversible mapping. Runtime entity IDs are session handles, never
persistent keys. Validate actor identity across layer reload and save restore.

## System work packages

| Package | Required implementation | First proof |
|---|---|---|
| Campaign registration | Dedicated converted-campaign identity, canonical level identifier, target registration/export recipe and campaign-gated startup. Audit required native global services separately from KCD2 story modules. | Cold start loads only the selected story and retains essential engine/player services. |
| New Game | Translate the opening slice of `q_master_main`, initialize source-derived Henry stats/equipment/time, activate the resolved opening profile, resolve quest assets, then start `q_skalitz` once. | Two successive New Games both start clean, with one mother, one Martin and no inherited quest progress. |
| NPCs | Convert the opening's actual souls, body/clothing, roles, factions, inventories, mortality and schedules. Bind dialogue speakers and authored workplace/interaction references. | Mother and Martin can be addressed and resume their correct role after load. NPC_NAI appearance alone does not pass. |
| Navigation and smart objects | Rebuild target navigation from converted collision; map doors, workstations, ladders, beds and interaction anchors. Test source navigation formats separately before considering reuse. | An opening NPC reaches an interaction position and traverses the relevant doorway. |
| Triggers | Convert shape, height, transform, parent, filters, entry/exit semantics, enable conditions and GUID links. Bind events to quest state. | Walking across one authored boundary advances the intended objective exactly once, including after loading while inside it. |
| World/layer state | Resolve parent profiles and conflicts, then apply explicit authored enable/disable sets. Convert state transitions, actor lifecycle and navigation variants together. | Reapplying the opening profile changes nothing; restoring another profile does not duplicate geometry or actors. |
| Quest logic | Import visible and hidden objectives, transitions, tracked assets, journal text and rewards. Translate predicates, timers, subscriptions, parallel branches, cleanup and cancellation. | One objective transition, journal marker and item reward survive a full restart and cannot fire twice. |
| Lua/behaviour compatibility | Inventory actual referenced functions, message types and enum semantics. Map object lookup, quest mutation, actor messaging, door state, inventory and cinematic calls to verified target operations. | The opening subset has zero unresolved required calls. No wholesale overwrite of KCD2 global scripts. |
| Dialogue | Reconstruct branches, conditions, commands, participants, skill checks, callbacks and objective actions from SQL/Skald data. Resolve localization and voice IDs. | Mother's opening conversation offers correct choices, records the selected outcome and advances the quest once. |
| Audio | Map voices separately from ambience/music/foleys. Convert event/control IDs, bank dependencies, switches, parameters, attenuation, loops and subtitle timing. | One original voice line plays from its actor with matching subtitle; it ends/cancels cleanly on interruption and reload. |
| Cinematics and facial motion | Convert cameras, actor bindings, sequence events, input locks, facial/phoneme tracks and exit cleanup. Existing native body rigs do not establish facial compatibility. | One opening sequence can complete or be skipped, restores control and reaches the same committed quest state. |
| Items and interactions | Register real quest items and source inventory presets; replace matching visual placeholders rather than adding interactive duplicates. Preserve quantity, ownership, theft state, locks and condition. | Picking up or handing over one quest item persists correctly across save/load. |
| Save/Load/Continue | Use native save slots and completion notifications, persistent native quest/entity/item state, plus versioned campaign metadata if a supported hook exists. | Save, quit the process, restart and Continue reproduce the opening state and allow further progress. |
| UI/localization | Integrate campaign selection, journal/objective markers, dialogue, loading errors and campaign-filtered save listings. Reuse target UI facilities. | The normal UI starts/continues the converted campaign without console commands. |

The native extension points for registering a new Skald project, routing the
retail New Game UI, serializing custom state and producing target initial state
are **early research gates**. Their complete workflow has not been verified.
If an engine-owned operation is unavailable, document the exact missing hook
and prove an alternative before building dependent quest content.

## Startup and restore contract

Define separate entry paths:

1. **New Game:** create a fresh campaign instance, initialize native state,
   establish identities, load the world/profile, wait for required actors and
   services, start the opening, and commit a first stable checkpoint.
2. **Load/Continue:** select a compatible save, let native restoration finish,
   resolve persistent references, reconnect transient subscriptions, restore
   presentation, and resume. Never run new-game initialization or regrant items.
3. **Restart/retry:** use an explicit checkpoint or fresh initialization path;
   do not infer intent from a level-load event.

Gate input release on required data, collision and actor readiness, not an
arbitrary sleep. Surface unresolved IDs and missing content in diagnostics.
Startup must be idempotent after a partial failure: no second actor, duplicate
inventory grant, duplicate timer or second opening dialogue.

Persistent state must cover:

- Campaign/run identity, content build and state-schema version, canonical
  level and compatible ID-map version.
- Henry's transform, stats, perks, inventory, equipment, ownership and condition.
- Quest/objective states, source persistent variables, branch choices and
  completed reward/action identifiers.
- Relevant NPC identity, alive/dead state, transform, inventory, schedule/role
  overrides and persistent interaction state.
- Active layer profiles, door/container states, collected/spawned objects,
  trigger enable/consumed state, world clock and persistent deadlines.
- Committed dialogue/cinematic checkpoints and chosen results. Transient audio,
  subscriptions and camera locks are recreated or cleared deliberately.

Prefer the existing native owner for every field. If a compatibility field
cannot be serialized natively, first test a supported persistent quest/entity
extension. A sidecar file is only a fallback design requiring save-slot identity,
atomic pairing and mismatch rejection; it is not the default plan.

Use stable boundaries for initial saves: before dialogue, after a completed
dialogue action and between objectives. Defer saves during non-resumable scene
transitions until a safe boundary; the save UI must not report success early.
Distinguish game-time deadlines from real-time waits, preserving the source
timer semantics across sleep, load and pause.

Use a committed action ledger or equivalent native persistent quest gates to
make item grants and objective rewards exactly-once. Loading an earlier save
restores that earlier ledger, not the current session's values.

Save compatibility policy: reject a different campaign, missing required
content or unsupported schema with a useful message. Permit only explicitly
tested migrations. Do not bind durable saves to disposable `kcd1_world_vNN`
development map names; choose a stable campaign level before release.

## Smallest playable opening and milestone order

| Milestone | Scope | Exit gate |
|---|---|---|
| G0: Native lifecycle proof | Tiny native quest/state on an isolated campaign test level; one persistent actor, item and trigger; target save/export/registration workflow. | Set state, save, quit, relaunch, load and change the restored state again. Prove New Game resets it. |
| G1: Opening dependency audit | Effective package precedence, SQL subset, `q_master_main` opening branch, `q_skalitz`, profile inheritance and reference closure. | Machine-readable manifest with all required dependencies resolved or explicitly blocked. |
| G2: Interactive opening | Henry at the authored start, correct initial world, mother and Martin, one usable door, basic collision/navigation, one dialogue/voice/subtitle and `speakWithFather`. | Play from a clean start to the first conversation and save/continue successfully. A development scene skip may be explicit here. |
| G3: One complete errand | Select one original errand after dependency analysis; implement acquisition/payment/handover, inventory ownership, journal updates and reward handling. | Complete it without console intervention; reload before and after handover without duplication. |
| G4: Complete `q_skalitz` | Remaining errands, optional branches, failures, schedules, timed events, reputation and scripted consequences. | Coverage report accounts for every objective and reachable branch in the selected source version. |
| G5: Original presentation and normal UI | Remaining opening cinematics, facial/lip synchronization, audio environment, menu New Game, save/load/Continue and failure handling. | Full original opening can be played through the normal UI, with no mandatory debug skip. |
| G6: Campaign continuation | Subsequent quest and world-state transitions, starting with the end of the opening. | State survives transition and the next quest is playable. Repeat dependency-driven expansion. |

G0 and G1 are the immediate work. Establish G0 before extensive content
conversion; otherwise we risk building quests around state that cannot survive
a reload. No completion date is asserted before these engine integration gates
and the audio/Skald export probes are resolved.

## Proposed source modules and outputs

Names below are planned files, not existing implementations.

- `tools/audit_campaign_dependencies.py`: read-only package/SQL/layer closure,
  hashes, counts, source precedence and unsupported-operation report.
- `tools/campaign_ir.py`: typed identities, objectives, predicates, actions,
  subscriptions, deadlines, profiles, conversations and source provenance.
- `tools/convert_campaign_tables.py`: target actors/items/roles/localization
  and stable ID-map emission with foreign-key and collision checks.
- `tools/convert_quest_logic.py`: supported expression/operation translation,
  native Skald output and explicit compatibility-adapter requirements.
- `tools/convert_campaign_entities.py`: quest objects, triggers, interaction
  anchors and links, including replacement of visual-only placeholders.
- `tools/convert_dialogue_audio.py`: sequence/line/speaker mappings and local
  voice/audio-control generation; no bundled game media.
- `tools/build_campaign_probe.py`: deterministic build manifest, isolated test
  campaign and narrowly scoped installation/rollback of generated overlays.
- `tools/audit_campaign_runtime.py`: read-back checks for actors, objectives,
  inventory, profiles, save identity and restored state; evidence per milestone.

Generate game content locally from both retail installations. Modding tools may
be consulted as format references during development, but are not setup inputs.
Commit converter source, schemas, synthetic tests and documentation. Keep SQL
dumps, extracted dialogue, voices, banks, cinematics and saves out of Git.

## Acceptance matrix

| Test | Required result |
|---|---|
| Fresh New Game twice | Same initial state; new run identity; no old quest variables or duplicated actors/items. |
| Save → quit process → Continue | Position, world time, quest phase, inventory and actor state match the checkpoint. |
| Save inside a trigger | Restore does not synthesize a second first-entry reward; actual exit/re-entry follows authored semantics. |
| Save before/after dialogue | Chosen response and quest side effects occur once; no stuck camera/input lock. |
| Save before/after item handover | Correct inventory delta and objective/reward state on both sides. |
| Timer across save/load | Remaining time follows the original game-time or real-time rule. |
| Walk away and return | Streaming/layer reactivation does not respawn dead actors, collected items or completed triggers. |
| Skip/interruption | Scene ends at a consistent checkpoint; audio stops and input returns. |
| Death/load and failed objective | Native recovery works and preserves the selected checkpoint's branch. |
| Older save after newer play | Earlier state returns completely, including gates and action ledger. |
| Missing asset/schema mismatch | Clear failure before partial campaign initialization or misleading save success. |
| Campaign separation | Converted saves cannot initialize the KCD2 story or overwrite unrelated campaign state. |

Initial release boundary: **the opening is playable and persistent**, rather
than a claim that the entire KCD1 campaign or existing KCD1 saves are supported.
