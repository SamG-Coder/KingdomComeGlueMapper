# Quest & NPC Tests

A **developer-only offline emulator** for converted Skald quest graphs and a
bounded subset of converted XGen controller behaviors. It reads the XML emitted
by the existing converters, including directly from a PAK, and executes scenarios
without launching KCD2. It has no consumer Setup integration and never installs
or changes the mod, game configuration or saves.

## Run the included converter scenarios

From the repository root:

```powershell
.venv/Scripts/python.exe tools/quest_lab_test.py demo --output outputs/quest-lab-demo
.venv/Scripts/python.exe tools/quest_lab_test.py run outputs/quest-lab-demo/01-scenario.json outputs/quest-lab-demo/02-scenario.json outputs/quest-lab-demo/03-scenario.json outputs/quest-lab-demo/04-scenario.json --output outputs/quest-lab-report
```

Choose a new demo directory if it already exists. Run output directories can be
reused. The tool needs only Python's standard library; `python` can replace the
repository virtual environment. No SDK, Steam or renderer is required.

Open `outputs/quest-lab-report/report.html` for assertions, source coverage and a
searchable execution timeline. `results.json` contains the complete evidence,
including snapshots, and `junit.xml` supports CI. Exit code is **0 only if every
scenario passes**. FAIL, TIMEOUT, UNSUPPORTED and ERROR all return 1. Unsupported
coverage is a JUnit error, not a successful skipped test.

The demos invoke the production `campaign_quest_graph`,
`campaign_training_runtime` and `campaign_schedule_lifecycle` emitters with
synthetic identities. They cover:

- A timed quest objective, subsequent source transitions and completion.
- Save/reload partway through a timer; equivalent modeled normal/skip clocks.
- Training preparation waiting for authorization and dialogue release.
- Worker stop and equipment cleanup acknowledgements before completion.
- Cancellation rejecting late preparation and lesson callbacks.
- Job replacement waiting for release, surviving save/reload, and rejecting a
  completion message carrying the old worker generation.

These are regression fixtures, not a replacement definition of a specific quest.
The training fixture supplies a synthetic combat contract and explicit arrival,
preparation and combat outcomes. It does **not** verify the equipment, position,
combat exercise or animation itself.

## Test converted package data

Audit every retail source quest independently of an installed mod, prioritizing
the opening and return quests while retaining every later source record:

```powershell
python tools/quest_lab_test.py campaign --kcd1 "D:/SteamLibrary/steamapps/common/KingdomComeDeliverance" --first q_skalitz q_returnToSkalitz --output outputs/campaign-review
```

This runs the production quest-state emitter and local probes, prints progress
for every quest, and atomically saves `checkpoint.json` after each record.
`campaign.json` is written when the audit finishes. Use a new output directory
for each run so interrupted work cannot be confused with an earlier result.
Checkpoints retain evidence; they do not yet resume execution. `--first` controls
review order, not story chronology. Actor behavior and cross-quest progression
remain separate verification requirements. Partial or blocked quests return a
nonzero exit code even when the audit itself finishes.

Accepted quest models are also compiled together into `compiled/`, with the
production cross-quest predicate binder. `linked-conversion.json` records the
resolved dependencies and remaining unsupported policies. The campaign report
lists excluded source records explicitly. This joint graph can be used as the
source for subsequent simulator scenarios; compiling it does not run the story
or establish native gameplay success. The generated files contain local game
data and belong in ignored outputs, not source control.

Run the package-wide audit, optionally comparing and dry-running **every** source
quest record through the production quest-state emitter:

```powershell
python tools/quest_lab_test.py audit "D:/path/to/gluemappertravel.pak" --kcd1 "D:/path/to/KingdomComeDeliverance" --source-graphs --output outputs/quest-lab-campaign-audit
```

`audit.html` and `audit.json` contain the new-game entry attempt, every installed
Quest, every exposed local callback probe, structural connections and a searchable
source-record checklist. `--source-graphs` emits temporary graphs in memory and
probes their public state transitions. It does not install quests or execute their
actor behaviors. Source records include test/development content. Converter
unsupported operations remain visible even when the emitted state graph runs.
The original graph/table disagreements behind rejected conversions are retained.

`EXECUTED` means an isolated diagnostic input ran without an interpreter exception;
it does not mean a quest passed or that gameplay can produce that callback. The
audit returns 1 when coverage is incomplete or a check is blocked. Its operation
counts are not quest completion or branch-coverage percentages. Existing `run`
scenarios provide assertion-based verification for specific behavior contracts.

Value support includes explicit `ConstantPort` defaults, boolean `IfFunction`
gates, bounded numeric addition/conversions, and class/count item descriptors.
Scenario inventories must be explicit maps of item classes to nonnegative counts.
Quality, ownership and item-instance descriptors are rejected rather than assumed.
Numeric conversions reject nonfinite values and overflow outside the modeled
32-bit range. Native item-acquisition events and inventory access still require
explicit scenario contracts; matching item counts never synthesizes those events.

Class/count `ItemDescriptorTrigger` nodes also accept an explicit modeled
acquisition stimulus:

```json
{"action":"item_acquired","inventory":"player-bag","item":"item-class-guid","amount":1}
```

The inventory must have a declared `Items` query snapshot. This action adds the
quantity and emits `OnAcquire` for active, matching inventory/class watchers;
`OnTargetAmountAcquire` additionally fires when the count crosses the requested
threshold from below. These are declared simulator semantics, pending native
differential validation. Ownership, health, stolen status, transfers and physical
loot creation are not inferred. Ordinary `object_snapshot` updates emit no
acquisition events. The production completion gate still checks all quantities
and the objective's active state before completing it.

Inventory a source directory or a single package:

```powershell
python tools/quest_lab_test.py inventory "D:/path/to/gluemappertravel.pak" --output outputs/quest-coverage.json
```

This lists graph definitions, input/output ports and unsupported operations for
each definition. Inventory does not execute a scenario and is not a pass report.
Only explicitly supplied packages are loaded; missing dependencies and ambiguous
definitions stay visible. Use `entry_file` to select one of multiple definitions
with the same name. Child module lookup prefers the host's `Definitions` files.
Identical duplicate definitions can be reused; differing definitions are not
chosen arbitrarily.

Point a scenario's `source` at that same directory/PAK and `entry` at its actual
graph name. Relative source paths resolve beside the scenario, not the working
directory. For example, the generated training scenarios can be reused against
the installed lifecycle graph by changing `source` and `entry`. Scenario inputs
and expectations remain explicit and must match that graph's ports.

```json
{
  "schema": 1,
  "name": "Quest starts but does not finish without its objective",
  "source": "converted-package.pak",
  "entry": "my_converted_quest",
  "steps": [
    {"action": "fire", "port": "activate"},
    {"action": "fire", "port": "start"},
    {"action": "advance", "duration": "2h", "mode": "normal"},
    {"action": "assert", "values": {"output.started": true, "output.completed": false}}
  ]
}
```

The public quest `start` port is a **diagnostic input** here. A successful result
does not establish that the opening controller or real dialogue reaches it. For
that claim, load the containing project/controller and test the upstream chain.
The runtime refuses to overwrite an internal state or inject an arbitrary
internal event through scenario input commands.

## Supported execution model

| Area | Implemented | Boundary |
| --- | --- | --- |
| Skald state | Boolean, numeric/string assignments and declared enum states; getters and change events | Unknown types/setters are unsupported |
| Control flow | `If`, boolean `Function`, `Control::Compare`, `TriggerSequence`, nested Project/Quest/Module/Level definitions | Other native functions/nodes require implementations or explicit external contracts |
| Time | Deterministic microsecond GameTime clock, Timer start/restart/stop and ordered deadlines | RealTime and native time-skip/LOD policies are not emulated |
| Controller behavior | Root/OnInit, sequences/selectors, conditionals/switches, loops, assignments, GameTime waits | Unsupported trees are not silently bypassed |
| NPC requests | Actual emitted mailbox consumer, availability updates and release callback logic | Native job arbitration, priority/preemption and physical acquire/release are external |
| Native messages | `InstantSendMessage` trigger fan-in, typed payloads, explicit receiver/asset bindings and routed controller mailboxes | `OnExec` records dispatch; receiver acknowledgement and native AI reaction are separate |
| External effects | Named graph contracts, pending behavior actions and supplied success/fail outcomes | No implicit successful movement, animation, dialogue, combat or equipment operation |
| Persistence | JSON-safe emulator state, timer deadlines, behavior frames and inboxes; source/contract hash checks | These are emulator snapshots, not KCD2 saves |
| Reporting | Condition operands/results, state changes, messages, timers, engine inputs, assertions, operation coverage and hashes | Passing assertions are not native-runtime verification |

Both `mode: "normal"` and `mode: "skip"` process modeled GameTime deadlines in
order. The mode is recorded for comparison; it deliberately **does not pretend**
to reproduce native Wait-menu scheduling, AI LOD or real-time budgets. A
`skipInLOD=true` behavior is reported unsupported. Faction, inventory/equipment,
perception, spatial triggers and dialogue-engine operations still need explicit
boundary contracts or dedicated implementations. Arbitrary game Lua is never
executed.

Trigger fanout follows XML order. `TriggerSequence` executes connected A..Z
outputs in order, draining each branch before the next. Zero-duration timers
run at a deferred boundary. State-change events precede `OnExec`; duplicate
state writes emit `OnExec` but not a new state-change event. These are declared
emulator semantics pending differential validation against the native engine.

Node counts and visited/unvisited nodes are coverage evidence, not branch or
campaign coverage percentages. An unsupported node anywhere in the loaded graph
or configured behavior program prevents PASS, even if unvisited. A raw converted
graph cannot expose source operations that the converter already omitted; pair
this report with the converter's own unsupported-operation report.

## Scenario commands

| Action | Required fields / meaning |
| --- | --- |
| `fire` | `port`: exposed trigger input, GameStart/Haste event or contracted engine callback |
| `set` | `values`: declared data inputs; cannot override internal or connected values |
| `advance` | `duration` such as `30s`, `2m`, `2h`; optional `mode` normal/skip |
| `assert` | One or more `values`, `states`, `events` dictionaries with exact expected values/counts |
| `await` | `port`, `timeout`, optional absolute event `count` (default 1); advances to deadlines and stops when satisfied |
| `save` / `load` | `name`: named emulator snapshot; no real save files touched |
| `behavior_start` | `controller`, `tree`, optional unique `task` invocation name |
| `behavior_set` | `controller`, `values`: explicit external/controller inputs, traced as such |
| `message` | `controller`, `inbox`, structured `value`, structured `sender` |
| `outcome` | `controller`, pending external `task`, `result`: success/fail |
| `assert_behavior` | `controller`, optional `variables`, `availability`, `tasks` expectations |
| `assert_messages` | `count` and/or `pending` (receiver, message_type, content dictionaries); checks the graph's undelivered queue |
| `native_result` | `id`, `node`, `result` (`returned` or `aborted`), optional `values` and ordered `events`; resolves exactly one pending call |
| `assert_native` | `count` and/or exact `pending` request list; checks native calls awaiting results |
| `object_snapshot` | `identity`, `facets`: replace an existing object's observed native facets; an explicit engine observation, not a simulated mutation |

Event/assertion history remains cumulative across emulator loads for diagnosis;
world state, pending timers, tasks and messages are restored. Snapshot restores
reject changed source/contract fingerprints. Exact task instruction addresses
are internal to that source revision.

External graph contracts declare their reason, input kinds (`value` or `trigger`)
and typed output ports. The scenario must explicitly supply any output value or
event it needs. See `02-scenario.json` generated by the demo. Changing `IsActive`
is observable in the timeline; it never auto-completes an engine action.

For behavior programs, add a `behaviors` entry with `id`, either an XML `file` inside
the source or a `path` to an external test fixture, optional initial `variables`, `callbacks` mapping callback names to
graph trigger inputs, and `contracts` mapping leaf node tags to a reason.
Contracted leaf operations block their task until an `outcome` step. Contracts
cannot replace an interpreted node or swallow a behavior subtree. Controller
variables are explicit; unknown expression variables do not default to zero.
Native scheduler acquisition/release counts in the NPC demo are scenario inputs.

## Native messages and asset bindings

`InstantSendMessage` accepts multiple `Exec` edges, an explicit `MessageType`, a
`Receiver`, and `Content_*` payload values. Each trigger captures its own payload.
The graph queues the message and emits `OnExec`; that event does not prove the
receiver handled it. Unrouted messages stay pending and prevent a scenario PASS.

Scenarios can map native aliases using `assets`, for example
`{"player_any":"player"}`. This also supports `AssetPort` defaults and asset
arguments on interpreted conditions. Nested module instance asset bindings
override the definition's default alias and stay independent between instances.
Connected or explicitly supplied inputs
override port defaults. An unbound alias raises UNSUPPORTED when evaluated.

To execute receiver logic, provide an explicit route for each receiver/type pair:

```json
{
  "message_routes": [{
    "receiver": "actor-17",
    "message_type": "fixture:increment",
    "controller": "npc",
    "inbox": "Inbox",
    "sender": {"id": "scenario-quest"}
  }],
  "behaviors": [{
    "id": "npc",
    "path": "receiver-fixture.xml",
    "variables": {"count": 0}
  }]
}
```

This is a scenario fragment. Routes do not start a behavior automatically: use
`behavior_start`, and assert its state/callback to verify actual consumption.
An existing receiver with the wrong inbox can accept delivery without handling
the message; waiting for its callback must still time out. Route senders are
explicit fixtures, not inferred native sender identities. Duplicate receiver/type
routes and unknown controllers are errors.

Delivery follows queue order at emulator settle boundaries. The queue, receiver
inboxes and controller state survive snapshots. Snapshot identities include asset
bindings, routes and external fixture bytes. Native thread scheduling and actual
perception/combat reactions remain unverified. External fixture paths resolve
relative to the scenario JSON and never require modifying the installed package.

The default operation budget is 100,000, configurable with `budget` up to
1,000,000. Infinite loops and zero-delay event cycles stop with TIMEOUT.
Expressions use an allowlisted AST evaluator, not Python `eval`.

## Dialogue logic

`ForcedDialog`, `FaderDialog` and `Dialog` definitions expose their declared
ports plus native request inputs. Multiple `EnqueueDialogue` requests are
retained independently. A request does not start a scene or choose a branch.

Scenarios supply explicit engine boundaries:

```json
[
  {"action":"dialogue_start", "dialogue":"talk", "request":true},
  {"action":"assert_dialogue", "dialogue":"talk", "choices":["accept","leave"]},
  {"action":"dialogue_choose", "dialogue":"talk", "sequence":"accept"},
  {"action":"assert", "states":{"done":true}},
  {"action":"dialogue_end", "dialogue":"talk"}
]
```

Here `dialogue_choose` supplies an executed-sequence outcome, including internal
spoken sequences. `choices` means eligible sequences in the current decision,
not a rendered list of player prompts. Entry/exit emit `BeforePlay`/`AfterPlay`;
sequence execution emits the XML's ordered trigger list. `EndDialogue` blocks
further choices but waits for explicit `dialogue_end` to release the scene.
Omitting `request` models explicit player initiation. Ending before a selection
does not emit its effects.

The bounded evaluator supports boolean `Port(...)`, `SequenceUsed(...)`, AND,
OR and negation, with validation of every branch before evaluation. Decision,
GoTo, root EndTopic and EndDialogue are modeled. History, pending requests and
active decisions survive snapshots. Unknown scripts, cooldowns, skill checks,
included decisions and nested EndTopic remain UNSUPPORTED; no successful engine
outcome or Lua execution is supplied implicitly. Actor availability, cameras,
speech and actual native sequence timing remain outside these tests.

## Native calls and comparisons

An unimplemented `Function` or `MemberFunction` with an `Exec` edge is a native
call boundary. Multiple incoming `Exec` edges are retained. To test its surrounding
logic, supply a contract with the **exact XML signature** and explicit interface:

```json
{
  "open": {
    "reason": "Explicit test outcome; native door physics is not simulated",
    "native": {
      "kind": "Function",
      "declaring_type": "wh::entitymodule",
      "method": "wh::entitymodule::OpenDoor"
    },
    "inputs": {"Exec": "trigger", "Door": "value"},
    "outputs": {}
  }
}
```

The contract interface is scenario evidence, not an automatically discovered SDK
schema. Each trigger queues a numbered request with a copy of its arguments.
It produces no automatic success event and changes no native world state. Supply
`native_result` only after that request exists. Return values must match the
declared types; completion events must be declared and explicitly listed. An
aborted call emits neither values nor events. Directly firing completion or
setting return values is rejected. Unresolved requests prevent PASS and survive
save/replay. Function calls, persistent engine effects and explicit stat queries
remain distinct boundaries.

Unknown native node inputs with multiple sources are retained in
`unresolved_native_bindings`. The loader does not guess whether they are data or
triggers. Reaching them is UNSUPPORTED; they no longer prevent an audit from
examining the rest of the package. Duplicate drivers on interpreted data ports
remain errors.

`Control::Compare` supports both value-producing `Function` and branching
`IfFunction`, using native operator names `Equals`, `NotEquals`, `Less`,
`LessEquals`, `Greater`, and `GreaterEquals`. Numeric comparisons reject booleans,
nonfinite numbers and invalid integer ranges. Boolean/string values support
equality; native reference types support equality of explicit nonempty identity
strings or null. Entity lookups are not inferred from display names. Native
enums and `TimeOfDay` remain unsupported; they are not compared as arbitrary
strings or seconds.

The bounded numeric subset also supports `AddFloat`, `MultiplyFloat`,
`DivideFloat`, and the observed integer/float conversions used by native money
helpers. Division by zero, nonfinite values and signed-overflow conversions
remain UNSUPPORTED. These use Python's finite-number arithmetic; bit-exact native
floating-point rounding has not been established.

`Round` accepts integral inputs only, where rounding modes agree. Fractional
inputs, including halfway cases, stay UNSUPPORTED pending native evidence.

## Object snapshots and collection logic

Use `objects` to test actor/object bindings and query arguments, instead of
supplying a getter result directly at an individual node:

```json
{
  "objects": {
    "actor-17": {
      "wh::rpgmodule::I_Soul": {
        "properties": {"Inventory": "bag-17"},
        "queries": [{
          "method": "GetState",
          "arguments": {"State": "stamina"},
          "result": 30
        }]
      }
    },
    "bag-17": {
      "wh::entitymodule::Inventory": {
        "queries": [{"method": "GetMoney", "arguments": {}, "result": 125}]
      }
    }
  }
}
```

This is an explicit observation fixture. It does not compute stamina or money.
The graph still has to resolve the correct object identity and declared type,
follow its `Inventory` property, and supply the exact query arguments. Contextual
queries require a matching `Context`; an observation for a different actor or
context cannot satisfy the request. Reads and snapshot replacements are traced
as engine observations. Missing objects/properties/query results are UNSUPPORTED,
never zero or false. Invalid result types and duplicate query records are rejected.

The bounded interface covers selected Soul/CombatSoul/Stash properties and the
observed `GetState`, `GetDerivedStat`, `GetStatLevel`, `GetSkillLevel`,
`IsBodyPartBleeding`, `HasWeaponInHand`, `GetMoney` and `Items` reads. Other native
methods remain unsupported. `Items` uses the existing item-class/count model;
quality, ownership and item instances are not represented. Snapshots and initial
fixture identities participate in save/replay checks. A pending native mutation
does not automatically modify an object snapshot. Legacy per-node contracts can
still supply query boundaries when `objects` is omitted; they cannot override
snapshot-backed reads when it is provided.

`MakeArray` preserves explicit A..Z input order and duplicates; sparse slots,
nested arrays and unknown collection types are unsupported. `ContainsElement`
checks exact identity/slot-name membership. The supported collection types are
Souls, LinkableObjects, Areas, SmartObjects and EquipmentSlotIds.

`Switch` selects an explicitly declared case or default and reads only the
selected value. It supports multiple output columns and boolean/integer/string
or declared-enum selectors. Primitive and declared-enum outputs are validated.
Native enum types absent from the loaded XML remain unsupported. Unselected
branches are still included in structural coverage, even though their values
are not evaluated during selection.

## Regression checks

```powershell
python -m unittest discover -s tests -p 'test_quest_lab*.py' -v
```

See [validation evidence](../../docs/quest-emulator-validation.md) for the checked
converter scenarios and installed-package tests. This tool lives entirely under
developer Tools and is intentionally absent from Setup and installer payloads.
