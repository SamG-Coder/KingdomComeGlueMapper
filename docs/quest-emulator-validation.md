# Quest and NPC emulator validation

Date: 2026-10-26. Developer tool: [Quest & NPC Tests](../tools/quest_lab/README.md).

## Verified implementation

- [x] Standard-library command line tool runs without the game or renderer.
- [x] Reads converted XML from loose files/directories and PAK archives.
- [x] Interprets emitted quest state/condition/transition graphs, including nested definitions.
- [x] Executes deterministic GameTime timers, deferred completion, cancellation and restart.
- [x] Preserves timer deadlines and controller tasks/mailboxes through JSON-safe emulator snapshots.
- [x] Rejects snapshots from changed graph, enum-definition or external-contract sources.
- [x] Executes the production-emitted NPC activity request consumer and release code.
- [x] Rejects stale worker completion after a replacement request.
- [x] Keeps movement/animation-style leaf actions pending until an explicit scenario outcome.
- [x] Makes unsupported operations, missing inputs and unknown ports non-passing results.
- [x] Bounds infinite loops and zero-delay timer cycles with TIMEOUT.
- [x] Produces searchable HTML, JSON evidence and JUnit reports with nonzero failure exit codes.
- [x] Separate developer Tools documentation and entry point; no Setup changes.

## Tests run

| Check | Result | Meaning |
| --- | --- | --- |
| `test_quest_lab*.py` | 63 passed | Interpreter, clocks, pending actions, safe expressions, snapshots, package/source audit, item-count operations, native messages, dialogue boundaries/branches/history, dependency resolution, error reporting and CLI |
| `test_quest_import.py` | 10 passed | Source identities, runtime objective reconciliation, include traversal, null tracking and patched markers |
| `test_campaign*.py` | 721 passed | Campaign converter regression suite, including the targeted suites listed below |
| `test_campaign_training*.py` | 90 passed | Existing converter training regression suite |
| `test_campaign_quest_graph.py` | 16 passed | Quest-graph conversion, runtime names/flags and objective callbacks |
| `test_campaign_schedule_lifecycle.py` | 21 passed | Existing NPC lease conversion regression suite |
| Four generated emulator scenarios | 4 passed | Real converter emitters with synthetic, redistributable input fixtures |
| Installed training lifecycle scenarios | 2 passed | Actual installed XML with explicit simulated engine outcomes |

The two installed-package scenarios read
`Data/gluemappertravel.pak` from the local Modding Tools installation and select
`gm_training_q_tutorial_swordOne_lifecycle`. They reuse the generic training
scenarios to check authorization/dialogue release, stop/cleanup order and late
callback rejection. No installed files or real saves were modified. Local
reports include the exact source-file and contract fingerprints.

Reproduction commands for the redistributable fixtures are in the tool README.
Local evidence from this run is under:

- `outputs/quest-lab-report/report.html` and `results.json`.
- `outputs/quest-lab-installed/report/report.html` and `results.json`.
- `outputs/quest-lab-report/installed-coverage.json` (158 discovered definitions;
  inventory, not 158 passing quest tests).

Game data and machine-specific scenario paths are kept out of version control.

## Complete quest inventory audit — 2026-10-26

The installed Modding Tools `gluemappertravel.pak` was audited without starting
the game or modifying the mod. All 287 effective KCD1 quest-table records were
also dry-run through the production **quest-state** emitter, using temporary
graphs in memory. This does not execute their NPC/controller programs or the
full campaign build pipeline. Development/test records are included and retain
their source group labels.

| Scope | Observed result |
| --- | --- |
| Installed Quest definitions | 6, all audited |
| Installed public callback probes | 527 isolated diagnostic sequences |
| Installed Skald definitions / edge checks | 816 / 8,036 |
| Missing source nodes or duplicate node identities | 0 found by the structural checks |
| KCD1 source records | 287, all accounted for |
| Source local callback probes | 31,780 across the 252 graphs the emitter accepted |
| Clean local state probes | 31 source records; this excludes actor logic and is not a playable-quest count |
| Partial source dry runs | 221; emitter reports outstanding tracking, completion or reward adapters |
| Source conversion rejections | 35; no fabricated replacement graphs were generated |
| Assertion-based installed scenarios | 5 passed |

Confirmed checks:

- [x] Rattay visit: premature talk does not complete it; arrival activates it;
  talk completes it; duplicate inputs and save/replay retain the expected state.
- [x] Theresa visit: the same ordering, duplicate-input and replay checks.
- [x] Sword tutorial state sequence: explicit invitation/lesson callbacks reach
  completion; waiting alone does not finish it; replay preserves the result.
- [x] Training lifecycle: authorization and dialogue release precede preparation;
  cleanup and worker-stop acknowledgements precede completion.
- [x] Training cancellation rejects late preparation and lesson callbacks.
- [x] Item-count matching rejects the wrong item class and insufficient counts;
  unbound inventories and unsupported descriptor formats cannot silently pass.
- [x] Every source record has a status and converter/emulator evidence in the report.
- [ ] A complete new-game-to-campaign-finish run. After adding native message
  transport, loading progresses beyond the horse-sale helper and stops at the
  guard dialogue `gm_guard_interaction_14861.EnqueueDialogue` interface.
  This is an emulator load boundary, not evidence that the game has invalid wiring.
- [ ] Opening quest inventory, rewards, proximity and dialogue-driven progression
  with complete engine-boundary scenarios.

Actionable source/converter findings include these four Main-group records:

| Source quest | Exact evidence blocking this dry run |
| --- | --- |
| `q_counterfeiters` | `checkOutRapotasHideout` (226): graph says hidden; effective table says visible |
| `q_recruiters` | `bailiff` (69), `sasInnkeeper` (72), `sasoInnkeeper` (75): graph says nonexclusive; table says exclusive |
| `q_talmberkPrepare` | Visibility disagreements for `beerNegotiated`, `campA/B/C`; objective 41 is `clearForestBandits` in the graph and `talkToBorek_campsDone` in the table |
| `q_konradKyeser` | Unresolved enabled `0.Out -> 1178.Asset` source edge; existing null-tracking resolution does not accept it |

Across all 35 rejections: 19 encounter an empty objective name, eight first fail
graph/table flags, four first fail graph/table declarations, two have unresolved
edges, and two old test/TechAlpha records lack `function_name`. Multiple mismatches
can exist in a record; the JSON preserves details rather than just the first error.
These are compatibility findings, not a claim that the original KCD1 quests fail.

The raw state-emitter reports 1,732 tracking entries, 1,557 completion policies,
348 reward-table groups and 12 mixed-exclusive joins requiring further handling.
These counts include map markers, development content and work normally handled
by later adapters. They are **not** a count of confirmed bugs in the installed mod.

Only `q_skalitz` and `q_tutorial_swordOne` from the source record set are present
as Quest definitions in the audited package. Four other installed definitions
are generated quests/wrappers. The checklist marks absent records separately
from failed checks; it does not install those quests.

Added emulator support is general: explicit `ConstantPort` defaults, boolean
`IfFunction` gates, equivalent-definition resolution ignoring XML whitespace and
empty include lists, numeric reward arithmetic, and class/count item matching.
Unmodeled native trigger fan-in is reported as unsupported, not invalid data wiring.
The automatic audit command continues after per-quest failures and never upgrades
an incomplete conversion to PASS because its emitted subset executes.

Local evidence:

- `outputs/quest-lab-campaign-audit/audit.html`: readable report and searchable 287-record checklist.
- `outputs/quest-lab-campaign-audit/audit.json`: hashes, every probe, conversion omissions and exact source disagreements.
- `outputs/quest-lab-campaign-audit/scenario-results/report.html`: five assertion-based scenarios.
- `outputs/quest-lab-campaign-audit/scenario-results/junit.xml`: machine-readable scenario results.

## Native message transport follow-up — 2026-10-26

- [x] Interpret `InstantSendMessage.Exec` as a trigger, preserving independent fan-in sends.
- [x] Capture type, receiver and `Content_*` payload at dispatch time.
- [x] Separate the dispatch `OnExec` event from receiver handling/callbacks.
- [x] Deliver to explicitly mapped controller inboxes and execute the existing `ProcessMessage` interpreter.
- [x] Preserve pending messages and receiver inboxes through JSON snapshots/replay.
- [x] Reject changed asset bindings, receiver routes and behavior fixture bytes during restore.
- [x] Keep missing/wrong receivers pending; wrong inboxes cannot satisfy a handling callback.
- [x] Resolve `AssetPort` defaults and asset values from explicit scenario bindings.
- [x] Test external receiver fixture files without modifying installed packages.

Four new assertion-based scenarios read the actual installed helper XML:

| Native helper | Checked behavior | Result |
| --- | --- | --- |
| `switch_gossip` | Both participants receive the correct payload; receiver consumption survives save/replay | PASS |
| `cancelperceptionignore` | Receiver, default delay and perceptible-object alias are preserved | PASS |
| `fightstart` | NPC attacker dispatches target and priority fields to its receiver | PASS |
| `fightstart` | Player attacker does not dispatch the NPC attack request | PASS |

The receiving controllers are explicit test fixtures. These results establish
message transport and consumption, not native gossip, perception or combat effects.
Evidence is in `outputs/quest-lab-native-messages/report/report.html` and its JSON/JUnit companions.
The earlier five installed quest/training scenarios also pass with this version.

## Generic importer repairs and dialogue checks — 2026-10-26

These are production conversion changes shared by the importer and developer
audit, without quest-name or location exceptions:

- [x] Stop interpreting `quest_function` callback rows as named predicate aliases
  in local conditions, linked conditions and dependency discovery. Preserve
  unconverted callback scripts as `objective_callbacks` with their original rows.
- [x] Reconcile editor objective names/hidden/exclusive flags with the effective
  runtime table by stable objective ID. Retain both versions and table provenance;
  use runtime names and flags. Unknown script references are not silently aliased.
- [x] Recognize patched map-only tracking destinations only when the runtime
  asset resolves uniquely. Retain the original edge and both marker IDs.
- [x] Recognize explicit null/null, zero-count tracking placeholders without
  inventing a collectible or destination. Missing rows, ambiguous targets and
  real counted assets remain unresolved.
- [x] Preserve duplicate-identity, invalid-boolean and unresolved-edge rejection.

The repeated audit covers all **287** source quest records:

| Measure | Before these repairs | After |
| --- | ---: | ---: |
| Conversion blocked | 35 | 22 |
| Emitted and locally probed, with explicit missing support | 221 | 234 |
| Local state probes executed with no emitter omissions reported | 31 | 31 |
| Isolated callback sequences | 31,780 | 35,892 |

All four previously identified Main-group records (`q_counterfeiters`,
`q_recruiters`, `q_talmberkPrepare`, `q_konradKyeser`) now reach the emitted-state
audit. They remain partial, with tracking/reward/behavior omissions reported.
The 22 remaining conversion blockers are 19 empty objective-name cases and three
unresolved tracking edges without corroborating runtime rows. The source set
includes development and obsolete records; these counts do not imply 22 broken
playable quests. Expanded coverage exposes 1,950 tracked-completion records,
1,751 completion policies, 378 reward-table groups, 12 mixed-exclusive joins and
two callback-table groups requiring further adapters; these are not new regressions.

Dialogue support now checks declared boolean conditions, used-sequence guards,
branch transitions, ordered effects and save/replay. Native entry, sequence
execution and scene exit remain explicit scenario outcomes. Requests alone
cannot complete a conversation. Scripts, cooldowns, skill checks and other
unmodeled semantics prevent a passing result.

Six scenarios read the **actual installed dialogue XML** and pass:

- [x] Cross-guard prerequisites, effect trigger, sequence-use lockout and replay.
- [x] Sword invitation “Let's go” route.
- [x] Sword invitation “Not yet” route: the authored reply still tells Henry to
  meet at the sheepfold; the emulator preserves that shared follow-up branch.
- [x] Sword-training acceptance through the normal branch.
- [x] Sword-training acceptance through the upset branch.
- [x] Sword-training refusal emits the refusal effect, without the exercise-start event.

Those scenarios use explicit condition values at each dialogue's interface;
they do not establish that native NPC availability supplies the same values.
The earlier nine installed quest/lifecycle/message scenarios also pass. The
794 regression tests above are 721 campaign tests plus 73 quest/importer tests;
the smaller targeted rows are subsets, not extra tests.

Current local evidence:

- `outputs/quest-lab-importer-audit/audit.html` and `audit.json`: refreshed full audit.
- `outputs/quest-lab-dialogues/report/report.html`: six dialogue scenarios and source hashes.
- `outputs/quest-lab-importer-audit/regressions/report.html`: nine earlier installed scenarios.

At that stage, the full new-game graph got past the guard dialogue interface and stopped at
an unmodeled native door function (`gm_room_rental.opendoor38.Exec`). Its multiple
trigger inputs are correctly reported as missing emulator support, not a proven
invalid-data defect in the imported quest. Full campaign execution is still incomplete.

## Native function boundaries, bindings and value checks — 2026-10-26

- [x] Queue native function requests with their exact XML signature and a copy of
  the arguments at each trigger; retain multiple incoming execution paths.
- [x] Require an explicit returned/aborted outcome for each request; reject
  premature, duplicate, wrong-node and incorrectly typed results.
- [x] Preserve pending calls through save/replay; pending calls prevent PASS.
- [x] Retain unresolved native fan-in as an explicit coverage gap instead of
  aborting the entire package load or guessing its port semantics.
- [x] Honor nested module instance asset bindings instead of using the definition's
  default alias. Reject conflicting bindings and keep separate instances independent.
- [x] Implement value and branch comparisons with the native `LessEquals` and
  `GreaterEquals` names, strict numeric/identity checks and explicit unsupported types.
- [x] Model the finite arithmetic and integer conversions used by native money
  helpers. Keep overflow, zero division and unmodeled native rounding outside the claim.
- [x] Label new-game results `PARTIAL_UNSUPPORTED` when only a prefix executes.
  Keep the full evidence in JSON and summarize coverage in HTML.

The full installed project now loads **2,312 nodes**. Its new-game stimulus
executes two nodes before the character-creation gate waits for
`kcd1_travel.arrived`. That is an unsupplied engine boundary in this diagnostic,
not proof of a broken importer or a completed new game. There are **1,919 explicit
unsupported entries** remaining, down from 2,578 when this pass first loaded the
complete project. These entries include related missing source ports; they are
not a count of independent gameplay bugs.

| Additional scenario checks | Count | Result and boundary |
| --- | ---: | --- |
| Rental door/chest requests: one-night, permanent, revisit and expiry-state events | 4 | PASS for unchanged call-node excerpts; incoming state events and native outcomes are fixtures |
| Native tiredness and branding helper conditions | 2 | PASS using installed helper XML with explicit engine stat values |
| Native general/rental affordability and decigroshen conversion | 2 | PASS below, at and above the price using installed helper XML with explicit inventory balances |
| Earlier quest, training, message and dialogue scenarios | 15 | PASS with their previously documented boundaries |

The rental excerpts retain all four original function nodes and record their
source file hash in `scenarios/provenance.json`; no copied function node was
rewritten. They do not execute the full rental dialogue, native opening geometry,
lock system or overnight clock.

**810 regression tests pass:** 79 quest-lab tests, 721 campaign tests and 10 quest
import tests. `git diff --check` passes. The repeated full source audit remains
287 records and 35,892 isolated callback sequences: 22 conversion blocked,
234 partial and 31 local-state-probed. This pass found emulator defects; no
additional production importer fault was established. Earlier generic importer
repairs remain covered by the campaign/import regression suites.

Current evidence under `outputs/quest-lab-native-functions/`:

- `audit.html` / `audit.json`: current installed-package and full source audit.
- `native-report/report.html`: four rental-call excerpts and two native conditions.
- `money-report/report.html`: two native affordability helpers.
- `regressions/report.html` and `dialogues/report.html`: 15 prior scenarios rerun.

The next coverage work is native object/member queries and collection/control
nodes, followed by the engine arrival and scene-completion boundaries. They must
remain explicit until modeled and tested; no automatic engine success is inferred.

## Object queries, collections and importer subject validation — 2026-10-26

- [x] Follow native object properties by explicit identity and declared type.
  Actor-to-inventory chains cannot read a different actor's fixture by accident.
- [x] Resolve observed query values by receiver, method and exact arguments,
  including context. Missing observations never become zero/false.
- [x] Validate query result types, reject duplicate observations and expose every
  read in the trace. Keep snapshot changes explicit and restore them on replay.
- [x] Preserve MakeArray order/duplicates and exact ContainsElement membership.
- [x] Evaluate only the selected Switch case/output, including declared-enum
  mappings. Report unknown native enum types and missing branches explicitly.
- [x] Validate importer inventory subjects as one Soul asset. Previously,
  `attach_dialogue` accepted any asset with the matching alias, including a door
  or inventory asset; it now rejects incompatible and ambiguous declarations.
- [x] Execute production-compiled inventory predicates for all six comparisons,
  three thresholds and five inventory counts: **90 combinations**. Another
  actor's items do not satisfy the bound actor's condition.

**827 regression tests pass:** 95 quest-lab tests, 722 campaign tests and 10 quest
import tests. Seven additional scenarios pass, together with all 23 earlier
scenarios (**30 total**):

| New evidence | What is confirmed |
| --- | --- |
| Installed `getmoney` | Actor-to-inventory binding, unit conversion, changed observations and replay |
| Installed `istired` | Correct actor's stamina, boundary at 30 and explicit stamina changes |
| Installed `hasmoney` and rental equivalent | Correct inventory and price comparison without replacing getter-node outputs |
| Installed `minfloat` | Both switch branches, equality, negative and fractional inputs |
| Training selector excerpt | Every authored phase mapping and default; unchanged selector and its source enum declaration |
| Rental membership excerpt | Empty, matching and nonmatching participant lists; unchanged membership node |

Object snapshots are scenario observations, not native state simulation. The two
excerpts record source hashes and preserve their original nodes; they do not
establish complete training or rental execution. Native fractional `Round`
semantics remain unmodeled; only integral inputs are accepted.

Current evidence is under `outputs/quest-lab-object-queries/`: `audit.html` and
`audit.json` for the complete audit; `checks/report.html` for seven new scenarios;
`regressions/report.html` for the earlier 23. `scenarios/provenance.json` records
the excerpt origins. This is still a developer-only tool with no Setup UI hook.

The repeated source audit still covers 287 records and 35,892 isolated callback
sequences (22 conversion-blocked, 234 partial, 31 locally probed). Structural
checks report zero missing-node/duplicate findings. The complete new-game graph
still loads 2,312 nodes and executes only its two-node prefix before waiting for
arrival. Its 1,595 remaining unsupported entries include 165 native-query nodes
that need explicit object observations; these are coverage gaps, not a count of
gameplay defects. Full campaign execution remains incomplete.

## Remaining verification boundaries

### Controller bridge checkpoint — 1 November 2026

Quest Lab now delivers converted AI concept signals to active native bridge
listeners. A regression converts source SetQuestObjective operations through
the production behavior compiler, wires the emitted bridge into the campaign,
and executes the controller to start and complete an objective. Replay after
graph restore, inactive listeners, and unknown notifications are checked.

An original-source controller conversion for `q_skalitz` and
`q_returnToSkalitz` emitted 389 behavior trees and withheld 1,730 across their
dependency closures. These counts include shared helpers, not just the two
quest controllers. Missing actor/profile/message adapters and blocked helper
dependencies still prevent complete execution; signal support alone does not
make either opening sequence playable. Local diagnostic conversion evidence:
`outputs/quest-controller-20261101/report.json`.

The return path now supports version-2 GameContextCheck with explicit Then/Else
branches and one live SetGameContext binding. The production-converter
regression checks a false branch changing quest state, a later true branch,
and a false branch again after restore. Multiple writers remain unsupported.
The original two source quest models expose 57 readable initial conditions
through their generated bridges, with zero interpreter coverage findings in
that state-only graph. The same source-operation audit retains 134 unresolved
operations; no complete original controller execution is claimed. Evidence:
`outputs/quest-controller-20261101/state-feedback-checks.json`.

The source-operation bridge and dependency scanner now share the full behavior
compiler's callback-owner rules. Only a registered behavior callback proves
that `__object.name` names the owning quest; ordinary NPC/helper scopes remain
dynamic. With actual source controller registrations attached, the two-quest
operation check converts 222 sites and retains 43 unresolved (previously 134).
Remaining failures include absent referenced quests, unsupported lifecycle
policies, and an objective-name mismatch. This fixes false dependency blockers
in normal campaign assembly without claiming controller execution. Evidence:
`outputs/quest-controller-20261101/registered-scope-checks.json`.

The state-operation scan also follows exact registered controller entry points
outside a quest's same-named file. Repeated registrations are scanned once;
unrelated helper trees are excluded and missing registered trees are reported.
A source-wide registration audit found 208 external entries across 71 records
(one separate controller-definition error retained). In the original
`ta02_lostHammer` controller, this exposes and converts 21 operations in
`q_LostHammer.xml` that the old same-name scan omitted; two lifecycle policies
remain unresolved. This is source-operation conversion evidence, not a played
quest. Local reports: `outputs/quest-controller-20261101/external-entry-audit.json`
and `external-hammer-conversion.json` in the same directory.

The reproducible `quest_lab controllers` command imports requested source
quests with their actual controller registrations, saves each source model,
and emits a joint state graph and quest bridges. A six-controller run covering
Skatliz, return, tutorials, escape to Talmberk, the campaign master and Lost
Hammer converts 397 sites and retains 92 unresolved sites plus the explicit
missing-quest dependency list. Counts cover all six controllers and do not
claim completion. Evidence: `outputs/quest-controller-linked-20261101/report.json`.
Tests cover failed-import exclusion, final graph hashes, interrupted checkpoints,
and protection against overwriting an earlier run (114 Quest Lab tests pass).

With `controllers --quest q_skalitz --follow-dependencies`, recursive static
reference discovery reaches 168 records: 159 accepted and nine blocked. The
joint bridge converts 11,138 quest-state operation sites and retains 862
unresolved sites. Failed dependencies stay listed rather than being retried or
silently dropped. This includes source include/authoring errors and unknown
literal quest references; dynamic expressions are retained without guessing.
Evidence: `outputs/quest-controller-closure-20261101/report.json`. The audit
finishes with a nonzero diagnostic exit status because gameplay remains
unverified. Cycle/missing-reference regression coverage brings Quest Lab to
115 passing tests. Discovery order does not establish story chronology, and
the emitted state bridges do not execute complete actor/controller programs.

### Inventory acquisition checkpoint — 1 November 2026

The emulator now supports explicit class-and-count acquisition stimuli for the
production converter's `ItemDescriptorTrigger` nodes. Tests cover the wrong
inventory, wrong item class, partial quantities, cancellation, and save/reload.
Replacing an inventory observation does not invent an acquisition event.

Checks using the original Skatliz `buyCharcoal`, `getCrossguard`, and
`collectBeer` objectives passed their quantity and actor assertions. These use
an explicitly declared source-item identity mapping as a fixture, not verified
registration of those items in the installed mod. Native acquisition ordering,
physical item creation, and the shop/dialogue path are still unverified.
Evidence: `outputs/quest-inventory-20261101/report.json`.
The full suite passed 2,332 tests with 17 skips.

The combined campaign now applies this same production binder when explicit
inventory mappings are supplied. A regression scenario executes acquisition
through the nested campaign quest and verifies the quantity gate and final
graph hashes; an unregistered mapped target is rejected. Without mappings, the
full source run records 1,950 pending tracked objectives across 265 accepted
quests, including non-item actor/location tracking and unsupported policies.
These are coverage requirements, not 1,950 verified gameplay defects. Evidence:
`outputs/quest-campaign-inventory-20261101/linked-conversion.json`.
All 106 Quest Lab tests pass after the integration.

A subsequent run reads actual KCD2 and installed-mod item definitions through
the production registry, including placeholder replacement identities. It binds
88 objectives, including 12 in Skatliz. All 88 pass empty-inventory, quantity and
completion checks in the joint graph, with zero interpreter coverage findings.
Inputs remain explicit acquisition stimuli; shop/dialogue/controller execution
and native ownership transfer are not tested. Evidence:
`outputs/quest-campaign-registered-20261101/registered-inventory-checks.json`.
The registration reader has regression coverage for placeholder exclusion,
replacement identities, and invalid installation paths (108 Quest Lab tests).

### Joint campaign execution checkpoint — 1 November 2026

Registered controller discovery now retains malformed work-program registrations
as unresolved dependencies instead of abandoning the entire quest import. Valid
sibling programs survive, while the original invalid row and error remain in
the source model. Strict profile conversion still rejects invalid registrations.
This is a generic registry rule, with no quest-name exceptions.

A source-data rerun accepts the state models for `q_escapeToTalmberk`,
`q_counterfeiters`, and `q_konradKyeser`, previously blocked by a malformed sibling
work registration. Together they expose 1,008 converted state-operation sites
and 64 unresolved sites. All three retain their work dependency blockers and
`executable=false`. Evidence:
`outputs/quest-controller-work-registration-three-20261101/report.json`.
The full suite passed 2,346 tests with 17 skips. These results do not establish
whole-controller execution or native campaign playability.

The recursive registered-controller audit from `q_skalitz` subsequently discovers
172 records: 165 accepted state models and seven blocked records (previously
159 accepted out of 168 discovered). It converts 12,285 controller state-operation
sites and retains 878 unresolved sites. The seven blocked records include missing
source registrations, a missing test behavior file, invalid source objectives,
duplicate behavior names, and unresolved graph edges. None are silently omitted
or treated as completed quests. Evidence:
`outputs/quest-controller-work-closure-20261101/report.json`.

Quest conditions with both authored `Then` and `Else` branches now convert even
when `failSubtMissing` is enabled: neither branch is absent, so the missing-branch
policy is inapplicable. Absent branches with that policy, dynamic policy values,
duplicate branches, and unexpected branch types remain rejected. A production
compiler-to-simulator regression executes both sides with the branches in reverse
XML order, checking failure propagation and a subsequent objective action.

Reprocessing the same 165 source models converts six additional sites, including
`q_tutorial_swordOne`, `q_theresa_brother`, `q_visitInBaths`, and three execution
cutscene conditions in `q_execExec`. Totals are 12,291 converted sites and 872
unresolved; this does not certify whole quest execution. Evidence:
`outputs/quest-controller-complete-branches-20261101/report.json` and
`newly-converted.json`. All 2,349 tests pass, with 17 skips.

### Shared controller helper execution — 1 November 2026

The behavior interpreter executes static `IncludeTree` calls using reachable
converted trees from its package source. Both build-fragment `AI/` and installed
`Libs/AI/` paths are recognized; ambiguous files and tree names are rejected.
Calls retain separate execution frames, propagate failure, and can suspend on
waits or explicit engine outcomes. Helper content participates in the behavior
snapshot fingerprint. Recursive calls use the existing execution budget.

The production compiler-to-simulator test calls two nested helper files, waits,
saves and restores the suspended stack, and completes the original quest
objective through the emitted signal bridge. Additional tests cover failed
helpers, unused trees, missing/dynamic includes, duplicate file candidates,
unsupported child overrides, recursive time limits, and changed helper code.
Included trees declaring local variables remain unsupported until call-local
initialization and persistence are implemented; they never silently share the
controller's global variables. Native include lifecycle parity is unverified.

### Concurrent controller branches — 1 November 2026

`Parallel` is now interpreted with explicit `All`/`Any` success and failure
policies. Each child keeps its own execution stack while sharing the controller's
existing variable environment. Waits overlap in simulated time. Child outcomes
wake the parent; a satisfied outcome cancels outstanding sibling stacks,
including nested parallel work and pending explicit engine requests. Late
outcomes for canceled requests are rejected. Saved snapshots retain all branch
stacks and deadlines. Mixed terminal results satisfying neither configured
policy are reported unsupported rather than assigned an invented result.

The emulator uses deterministic task creation order, not native scheduler
arbitration. It processes a returned branch before allowing a later sibling to
perform more effects. Cancellation does not claim native engine cleanup or run
unimplemented exit handlers. A production-compiled regression completes an
objective on one branch and verifies that a later conditional on another branch
sees it and starts the next objective. Other tests exercise overlapping waits,
save/reload, recursive cancellation, and explicit external outcomes. The actual
sword-tutorial `onUpdate` contains parallel branches, but its remaining actor,
profile, messaging, gate, and lifecycle dependencies still prevent claiming a
complete tutorial run.

### Repeating message handlers — 1 November 2026

The source sword tutorial uses `Loop count=-1` around its `ProcessMessage`
listeners. The simulator now supports successful-body repetition with positive
integer counts and the infinite `-1` form. Loop counters and suspended child
frames survive save/reload. Waiting handlers yield to other parallel branches,
and parent cancellation stops further message consumption. An instantaneous
infinite loop still exhausts the execution budget instead of hanging the tool.
Zero counts, invalid schemas, and failed loop-body policy remain unsupported
pending native semantic verification.

Regression scenarios cover finite successive message processing, infinite
listeners canceled by parallel completion, and actual graph-message routing
through `Runner` across save/reload. State rewinds on reload; diagnostic event
counts retain replay history, consistent with the existing graph trace policy.

### Behavior-to-controller messages — 1 November 2026

`InstantSendMessageToNPC` now sends through the same copied-payload queue as
Skald `InstantSendMessage`. It accepts an explicit message type and receiver,
with either a dictionary-valued variable or named `field(expression)` payload
initializers. The existing scenario routes select the receiver controller,
mailbox and explicit sender metadata. Dispatch completes without assuming an
acknowledgement; missing routes retain the message and prevent a passing run.

Tests route immediate sends from one behavior controller into a repeating
receiver, verify subsequent messages after save/reload, and check payload-copy
semantics. Duplicate fields, malformed initializers, non-structured payloads,
and combined variable/initializer policies remain rejected. This adds simulator
delivery, not native message type registration, native NPC scheduling, or
synchronous `SendMessageToNPC` request/reply semantics.

The source campaign compiler now assembles 265 accepted quest records together,
resolving 336 cross-quest predicates. The other 22 records remain explicitly
excluded with their conversion errors. This includes the original Skatliz
`practiceFencing` dependency on `q_tutorial_swordOne` completion.

Three scenarios against that complete compiled project pass all 18 assertions:
waiting for tutorial completion (including failure and save/reload replay),
tutorial completion before objective activation, and cancellation followed by
restart. Inputs use declared public quest ports. Tutorial completion is an
explicit diagnostic stimulus; the combat exercise and its controller are not
executed by these scenarios. The complete campaign is not marked finished.

The tests exposed a generic interpreter coverage bug: forwarded child trigger
outputs executed correctly but were reported unresolved. The checker now
recognizes wired trigger outputs while still auditing their original sources;
forwarding an unknown source does not make it supported.

Local scenarios and HTML/JSON evidence:
`outputs/quest-campaign-linked-20261101/fencing-checks/`.
The scenario generator is `quest_lab.linked_checks.scenarios`; it accepts only
a single literal quest-completion predicate and rejects compound conditions
rather than simplifying them.

- [ ] Differential validation of emulator event ordering against the native runtime.
- [ ] Native Wait-menu, AI LOD and scheduler priority/preemption behaviour.
- [ ] Native movement, scene release, animations, bed use and combat exercises.
- [ ] Actual item creation/equipment and faction/perception mutations.
- [ ] Native KCD2 save serialization and restoration.
- [ ] Complete source-controller/dialogue chain reaching each tested quest input.
- [ ] Execution support for every node type in the installed package.

In particular, these checks do **not** establish that Van­yek's native agitation,
Talk availability or delayed arrival is fixed. They establish that the selected
installed training lifecycle responds correctly to the tested explicit inputs
under the documented emulator semantics. Unknown engine systems stay visible in
coverage instead of being silently assumed to succeed.
