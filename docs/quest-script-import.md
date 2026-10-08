# Quest and script import

The first implemented stage imports patched **retail KCD1** quest data into a
structured migration model. A separate bounded state probe now emits and runs
one native Skald assignment; the importer does not start the opening quest.
The installed games and the published setup release
are not changed by this command.

```powershell
python tools/quest_import.py --kcd1 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance" --kcd2 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2" --quest q_skalitz --output outputs/quest-import
```

Choose a new output directory on each run. `--quest` is explicit and required.
There is no implicit bootstrap quest. Optional `--bootstrap` arguments accept
behavior paths relative to `Libs/AI` when extra source scripts are needed.

## Generic campaign linking

```powershell
python tools/campaign_linker.py --kcd1 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance" --level rataje --output outputs/campaign-links.json
```

`--entry-action` selects a different source UI action when needed. The current
adapter recognizes the retail Lua entity-lookup/message-dispatch idiom; unknown
or ambiguous dispatch scripts fail rather than selecting a named quest by
convention. It preserves the full action graph for later lifecycle translation.
This is static discovery, not evaluation of arbitrary Lua or event reachability.

The linker indexes mission and layer entities, follows their explicit links
recursively, and matches linked QuestObjects to retail database identities.
GUIDs and IDs must agree; ambiguous layer variants remain unresolved until a
profile selects them. Original trigger geometry, properties, link labels and
source locations are preserved. No source entities are spawned by this tool.

Behavior operations are classified into reusable inventory, equipment, quest,
objective, profile, trigger, subscription and actor-state families. Every record
retains its tree position and ancestor conditions. These are **source operation
records**, not implemented native adapters: finding a CreateItem under a branch
does not mean that it belongs in the starting inventory. Clothing presets and
item assets still require dependency resolution and native conversion.

The real level run discovered its controller from the UI action and followed
11,833 links across 9,941 entities, reaching ten QuestObjects. No explicit links
in that closure were unresolved. This is a reference closure, not an activation
list: it includes quests reached through shared world entities. Source level
archive patch precedence and profile activation remain separate unfinished gates.
All generated records are non-executable. New Game and restoration remain
separate required adapter entry points; no inventory is granted on either yet.

## Implemented

- Resolve whole-file retail patches in revision order and retain archive hashes.
  The same resolver serves the existing campaign setup.
- Preserve graph nodes, ordered behavior trees, raw expressions, Lua bodies,
  persistent variables, and edge ports/enable flags.
- Keep executable `Root`, disconnected `ForestContainer`, and duplicate editor
  data separate. Disconnected nodes are retained for inspection, not executed.
- Follow static behavior includes recursively, detect cycles, and validate tree
  names. Keep dynamic include expressions as unresolved dependencies.
- Extract quest-scoped database rows for objectives, transitions, tracked assets,
  NPCs, items, places and rewards. Reject mismatched graph/database objective IDs
  or names rather than silently choosing one version.
- Resolve null tracked-asset edges only when the corresponding database row
  proves that the counted asset is empty and the map marker is separate.
- Read target node examples from retail KCD2's base `Scripts.pak`. This catalog
  includes user-defined module tags; it is not a native API schema or proof that
  similarly named source/target operations have equivalent behavior. Target
  patch precedence must be resolved before executable emission is implemented.

Output files are `quest-ir.json`, `kcd2-node-catalog.json`, `retail-sources.zip`,
and `summary.json`. They contain locally extracted game content and belong in
ignored output directories, never in a source release.

## Local validation, 8 October 2026

The real opening import found quest ID `31662772`, **109 objectives**, **71
transitions**, **26 tracked assets**, **8 NPC assets**, **9 item assets**, and
**6 place assets**. The graph and database objective identities agree.

The import retained 50 source documents. The script documents contain 536 trees,
330 operation tags, 209 Lua sites and 100 persistent-variable declarations.
These counts include all trees in each included helper document and the master
bootstrap; they are an overestimate of the opening's reachable execution paths,
not a claim that every helper runs during the opening.

Three graph links use node `0` for an empty counted asset. Retail database rows
confirm separate map assets `391`, `509` and `806` for objectives `48`, `173` and
`805`. They are recorded as null-asset semantics without rewriting the source
edges. No unexplained graph endpoints remain in this opening import.

Fifteen include sites use runtime-selected files or tree names. They remain
explicitly unresolved. A runtime adapter and dependency evaluation are required;
dropping them would lose cutscene callbacks and actor behavior.

## Native execution proof, 8 October 2026

`tools/skald_state_probe.py` lowers one persistent scalar Boolean assignment
from the imported runtime tree into KCD2 `State` logic. Document, tree, variable
and project names are caller inputs. The rule requires an explicit Boolean
default and exactly one supported assignment; it rejects ambiguous assignments,
unknown defaults, other types and compound expressions. It preserves source
provenance and hashes the emitted XML. It does not execute source branch guards.

```powershell
python tools/skald_state_probe.py --ir outputs/quest-import-003/quest-ir.json --document libs/ai/quests/q_master_main.xml --tree onUpdate --variable gameInitialized --project GlueMapperConvertedState --output outputs/quest-live/converted-state
```

This real retail source assignment (`$gameInitialized = true`, default `false`)
was converted and executed in the KCD2 modding-tools development game on
`kcd1_world_v37`. The generated graph was installed as an isolated loose file
under the tools installation's `Data/Quests`, then selected as its diagnostic
Haste graph. The engine log recorded:

```text
<Trace> name:'GlueMapperConvertedState.source_state_proof' false
<Trace> name:'GlueMapperConvertedState.source_state_proof' true
<Trace> name:'GlueMapperConvertedState.source_state_proof' true
```

These are an initial read, the assignment callback, and a subsequent read.
The native commands were `wh_concept_HasteTrigger probe_read`,
`wh_concept_HasteTrigger probe_assign`, then `probe_read` again. Trigger names
are relative to the selected Haste graph. `wh_concept_HasteFile` was restored
to `Quests/Debug/Haste.xml` and the graph reloaded after the test.

The local conversion manifest and log observations are in
`outputs/quest-live/converted-state/`. Generated game content stays ignored.
The regression suite passed 114 tests, including synthetic tests with different
variable names and both Boolean assignment values.

This is native execution proof for one operation, not campaign startup proof.
The diagnostic graph warned that its project entity was absent. Native shipped
levels bind a `Concept` entity through `Properties.fileConcept`; that registration
has been identified but not yet tested for this converted project. The native
`State` definition marks state as affecting saves, but a save/restart/load test
has not been performed. No source conditions, objectives, inventory or clothing
operations were activated by this probe. Its manifest remains
`campaign_ready: false` and `save_round_trip_validated: false`.

A subsequent live test spawned a `Concept` entity named after the generated
project, with `fileConcept` pointing at the generated XML, while retaining the
default Haste graph. `wh_concept_FullReload` discovered and deserialized both
graphs. Binding then failed with the specific engine error:

```text
Entity 'GlueMapperConvertedState' for concept graph Project node needs to be smart object of valid type
```

Thus a file reference is sufficient for graph discovery but is not sufficient
for project registration. The next adapter must establish the project's native
smart-object identity/type, not merely spawn a `Concept` entity. This probe
entity was removed after capturing `entity-binding-evidence.json` locally.

The follow-up test resolved that error by matching the shipped level's actual
two-entity arrangement: `Barbora_concept` is the file loader, while `Barbora` is
a separate `SmartObjectHolder` matching the Skald project's name. The holder's
native Lua default type is `DEF0005E-0000-0000-0000-DEF00000005E`.
Spawning the equivalent pair for `GlueMapperConvertedState` and reloading loaded
the custom graph without the project binding error. Console read-back confirmed
`holder=SmartObjectHolder loader=Concept`. The normal Haste graph remained
selected. Evidence is in local `entity-pair-evidence.json`.

`project_entities()` now emits this pair with deterministic GUIDs as
`project-entities.xml` alongside a newly generated probe. This is a mission
fragment: the level packager must assign unique numeric entity IDs and positions
before installation. Runtime spawning/reload has been tested; a fresh level load,
New Game dispatch and save/load have not. The suite now passes 115 tests.

`tools/build_skald_level_probe.py` now builds an isolated level copy, allocates
numeric IDs for the pair, and wires a diagnostic `GameStart.OnStart` to the
converted assignment. A fresh development-process launch of
`kcd1_quest_probe_v1` completed and automatically traced
`GlueMapperConvertedState.source_state_proof true` without a Haste trigger.
However, console lookup reported `holder=nil loader=Concept`, and the engine
warned that the exported level lacked concept graph preprocessing. The packaged
holder is therefore not yet a successful persistent binding. Runtime-spawned
binding and packaged startup activation are distinct partial results, not a
finished campaign. Evidence is in `fresh-level-evidence.json`; 116 tests pass.

The missing packaged holder was resolved in `kcd1_quest_probe_v2`. The visual
base map has no explicit entity IDs, so the original allocator gave the holder
ID 1. Allocating above the low runtime range and existing entity IDs fixed the
fresh-load failure. A new process loaded both entities, reported
`GLUE_V2 holder=SmartObjectHolder loader=Concept`, and automatically traced the
assigned state as `true`; the custom project binding warning was absent. The
separate concept preprocessing warning remains. These observations are preserved
in `fresh-level-v2-evidence.json`. The suite passes 117 tests, including ID
allocation with missing IDs and existing high IDs. This still does not establish
save/load persistence or source branch eligibility.

## Initialization ordering

The real `gameInitialized` assignment is guarded by `$gameInitialized == false`
inside an `AtomicDecorator`. It follows world-profile initialization, quest
activation/start operations and an objective condition. It is a completion flag,
not a substitute for those operations. The diagnostic GameStart probe must not
be promoted unchanged into campaign startup.

Conversion manifests now include `source_execution_context`: control ancestors
and preceding Sequence subtrees with their original paths, attributes and nested
branches. This preserves dependencies without treating conditional operations as
unconditional grants. The actual selected assignment has 14 preceding subtrees
across its enclosing sequences, including 63 EnableProfile, 8 SetQuest and 19
SetQuestObjective nodes. These counts describe source structure, not operations
proven to execute. Local evidence is `outputs/quest-live/initialization-context/`.
The 118-test suite covers retaining a condition around a preceding operation.

The shipped `objectiveTest/super_quest.xml` provides the next native reference:
a `Quest` module exposes its `Progress` output from a State whose TypeT is
`wh::questmodule::QuestProgress`, with `SetActive` and `SetDone` transitions.
Objective declarations and their logs are separately typed. Matching these
structures is still required; mapping all KCD1 ActivateQuest/StartQuest calls to
SetActive without checking their separate semantics is not validated.

## Native quest progress test

`tools/skald_quest_probe.py` generates a diagnostic native Quest module from the
imported retail quest identity and a caller-selected project name. It validates
the quest name/ID against the imported table row and records that provenance.
It exposes a typed QuestProgress State through the native Progress output.
Manual diagnostic triggers set Active, Done or Failed; these are target tests,
not claimed equivalents of source ActivateQuest/StartQuest semantics.

The generated q_skalitz module was loaded in the development game on
kcd1_quest_probe_v2. Read/start/complete triggers produced None, Active and Done
respectively. The engine also logged PlayAudio quest_started and quest_completed.
This demonstrates that the module participates in native quest lifecycle events,
but does not establish journal presentation, objectives, rewards or persistence.
The Failed transition is generated but has not been live-tested. Evidence and
generated XML remain local under outputs/quest-live/quest-progress. The original
Haste graph was restored and temporary diagnostic holders removed afterward.
The regression suite passes 120 tests.

The optional `--objective-id` diagnostic now resolves an objective by source ID
and quest ID, emits its native declaration and binds a typed State to Progress.
The source objective 5 (`speakWithFather`) loaded and traced Active then Done
through manual triggers. Evidence: outputs/quest-live/objective-progress.
Source interactions, localization, hidden-state policy and rewards remain
unconverted; the diagnostic must not be presented as a playable objective.

## Authored opening world

Quest probes alone do not load the original people, voices or world state.
`tools/campaign_world_profile.py` resolves a named profile from the retail level's
whdata_1 GameProfileManager and its layer files, then joins NPC and NPC_Female
entities to whdata_0 SoulList records by entity GUID (hex entity versus decimal
soul serialization). It preserves full source entities, links and soul records,
including body, initial clothing, inventory and AI references, without activating
or converting them yet.

The actual q_skalitz_normal run resolved 73 layers and 55 human NPC souls without
missing layer/soul references. The layers also contain 56 SequenceTriggers,
24 CameraSources, 12 AudioAreaAmbience entities, 4 AudioAreaEntities and 2
AudioTriggerSpots, among other authored objects. This is one source profile,
not the complete active world or dependency closure. Base archive patch
precedence, global layers, navigation, dialogue/voice assets and target runtime
conversion remain required. Local evidence is
outputs/quest-live/skalitz-world-profile-v2.json. The suite passes 122 tests.

`tools/campaign_population.py` connects these per-instance soul records to the
existing appearance table resolver. It uses authored CharacterBodyDescription
and InitialClothingDescription values, retains original placement/link records,
and records inventory, voice and AI dependencies. It does not replace those
values with Martin's appearance or a generic NPC. The first run resolved parts
for 54 of 55 humans, spanning 91 distinct clothing records. ska_e3_dicePlayer has
no authored body and remains unresolved; no fallback was guessed. Inventory and
voice IDs are references only, not converted payloads. Native clothing registration
currently remains outfit-specific and must be generalized before this population
can be installed. Evidence: outputs/quest-live/skalitz-population.json. 123 tests
pass; neither this manifest nor the world profile is marked executable.

The native clothing builder now accepts `--registration-profile` for other
people. A profile supplies gender, default_body, underwear, garments (each maps
to [ArmorType, ArmorArchetypeId, region, layer, final]), assembled_armor_type,
assembled_archetype and assembled_layer. It validates the source body gender and
requires every garment role. The existing Martin profile remains its default;
other people cannot silently use it. This is a reusable registration path, not
proof that arbitrary mappings or meshes are visually compatible. Development
overlays still require explicit inspection before replacing existing files.

A source/native archetype-name comparison found exact names for 72 of the 91
garments. Nineteen use source archetypes absent in KCD2, primarily FeetBoots,
_UPPER_BODY_4 and LegsChainTrousersLong. These require explicit native mappings;
even exact archetype names do not establish correct region partitioning or layer
order. Local comparison: outputs/quest-live/clothing-archetype-match.json.
The suite passes 125 tests; population installation remains incomplete.

The next asset build packaged ska_motherOfHenry on the native female rig under
campaign_mother_v1 (453 files). Its garment list matches the authored opening
record: cotte_001, s2_boots_001 and s2_surcotte_004. This is asset packaging only;
native registration and in-game appearance have not been verified. Shipped
female components provide F_SimpleDress (archetype 27, torso layer 5) and F_Shoes
(archetype 97, feet layer 2); imported garment overlaps still need validation.

The clothing builder also accepts --output-data to stage a complete registration
in a new folder. It copies packaged character assets and reads native templates
from the tools installation while writing all generated overlays to that folder.
The existing martin_v1 package successfully staged into
outputs/quest-live/staged-registration-check without replacing installed overlays.
Staging is not runtime installation and does not merge multiple character overlays.

`tools/merge_population_package.py` now assembles staged registrations. Shared
native XML records are deduplicated, distinct character records accumulate, and
Storm task sources merge by path. Conflicting named definitions, duplicate
character namespaces/soul identities and differing assets at one path fail
before publication. The first real staged-package assembly produced 1,008 files
and five overlays from the existing Martin diagnostic; this is not a populated
opening. Synthetic merge tests cover distinct people and shared Storm tasks.
128 tests pass. The output stays local and is not automatically installed.

The mother registration and a rebuilt Martin source-person registration now
assemble together in outputs/quest-live/parents-package-v2: 1,472 files, nine
overlays, two source soul identities, two appearance rules and two outfit
inventory presets. Read-back confirmed both records survive merging. The mother
profile uses native female dress/shoe roles but garment overlap has not been
visually validated. These soul registrations still use diagnostic brain/faction
defaults and outfit-only inventories; they are not the authored AI/inventory
initialization required for the opening.

The initial parents-package-v1 is rejected: a failed rebuild had copied an old
clothing report without its overlays. Staging now excludes old registration
reports, permits byte-identical regenerated skin files, and assembly requires
every referenced overlay to exist within the staging root. A regression test
proves stale reports cannot publish an output package. 129 tests pass.

### Authored inventories and placed identities

`campaign_inventory.py` resolves the selected population's six retail inventory
definitions, including weighted preset references and random-amount fields.
All references resolved, with 21 distinct item dependencies. Selection has not
been evaluated and native inventory emission is not implemented. Native KCD2
InventoryPreset modes require semantic comparison before translating the source
priority/max-items/restock rules; copying every candidate item would be wrong.

`campaign_actor_placement.py` emits placed-actor and version-8 soul fragments
for registered source people. The format was compared with trosecko's shipped
objects_mission0.xml and whdata_0: shared soul, instance soul, entity GUID and
name are separate identities. Source 64-bit entity GUIDs are normalized to
KCD2's 8-4-4 notation without changing their value. Authored transforms survive.
Duplicate identities and mismatched entity/soul bindings fail conversion.

The parents package produced two placements in
outputs/quest-live/parents-placement-v1; 53 unregistered actors remain explicitly
omitted. Old AI links and per-instance initialization are retained as pending
dependencies, not attached as executable KCD2 behavior. These are uninstalled
fragments, not a populated campaign. All 133 tests pass; live placement and
initialization have not yet been validated.

### Native placement live load

`build_population_level.py` builds an isolated copy of the converted world with
the generated placed entities and whdata_0 soul bindings. It rejects entity
identity collisions and bases that already contain soul data rather than
overwriting them. kcd1_population_v1 uses kcd1_world_v43 and the two-parent
placement fragment. Development overlays were backed up under
outputs/quest-live/parents-install-backup-v1 before installing the merged package;
retail files and existing levels were not changed.

The first load exposed a real registration bug: the clothing builder assigned
the male soul archetype to every person. Mother therefore failed native STORM
body/head/hair selection despite having female components. The builder now
resolves the gender-matching NPC archetype from the installed native table.
parents-package-v3 contains this correction. After a fresh process launch,
System.GetEntityByName resolves ska_fatherOfHenry and ska_motherOfHenry with
classes NPC and NPC_Female, actor interfaces and the original entity IDs.
The gender selection errors no longer occur. Evidence:
outputs/quest-live/parents-first-load.log and parents-second-load.log.

Physics settled both actors below their authored Z coordinates (father 63.1856,
mother 63.3593); the source positions were 63.890957 and 63.96954. Visual placement
and collision still need inspection. Native AI reports missing level-specific
Tables/ai/SmartObjectAnimations.xml and an empty exported-level identity. These
are unresolved world-initialization dependencies, not successful AI conversion.
The actors still use diagnostic brains/factions and outfit-only inventories.
136 tests pass. No quest interaction, dialogue/voice, original schedule or save
round trip is claimed by this live placement test.

## Next implementation gates

1. Prove custom Skald project registration and isolated startup in retail KCD2.
2. Extend the proven Boolean assignment rule into a bounded opening slice,
   including native objective operations,
   preserving ordering, conditions, timers, parallel branches and cancellation.
3. Bind source actors, items, layer profiles, triggers, dialogue and callbacks.
4. Start the opening once through the native New Game lifecycle, and verify
   objective state through a save, full process restart and load.

Until these pass, `executable` remains false. A successful import is not a
playable quest, and the existing Play KDC1 route continues to be a world startup.
