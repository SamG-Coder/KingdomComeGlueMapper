# Parent sleep and horse-hand hay investigation

Investigated on 23 October 2026 from `main` commit
`c8bcbd69c975823808d7d3e7e48a8c72883793d3`, on branch
`investigate/parent-sleep-horse-hand-hay`.

The second subject is the **horse-hand NPC's scheduled hay-unloading job**,
not horse movement, riding or player feeding.

## Results and evidence limits

| Finding | Evidence | Disposition |
| --- | --- | --- |
| The authored loft ladder has no navigation-generation step. | Loft conversion adds the interactive ladder and animation transform; its stage never reads or writes `recast.pak`. | Still unresolved. The complete NPC route needs validated landings and upstairs navigation. |
| Imported ladders receive new entity GUIDs while their original navigation references are copied. | The ladder converter changes identities; the navigation packer preserves source mesh payloads. | Still unresolved. A new read-only audit identifies source/destination mismatches across every stored mesh pair. |
| Loft bed scheduler cloning preserves a donor `Position` when present. | Reproduced with a synthetic native donor through the real conversion and repair paths. Other bed converters already discard this attribute. | Corrected for fresh conversion and existing marked lofts. Its effect on the installed parents is not established. |
| Previously converted hay jobs can receive a newer behavior without its required approach-point graph. | Existing jobs are excluded from the idle-schedule conversion path, while the resource publisher emits the latest behavior. | An owned-job migration now runs before behavior resources are republished. |
| A fully current hay job may still fail at movement or animation execution. | The preceding playtest reported pitchfork pickup/cart approach followed by standing idle; the later alignment correction was not verified in play. | Requires a native execution trace after confirming the installed package contract. |

This investigation had source code and synthetic fixtures, not the user's game
installations, installed PAKs, save state or a running KCD2 bridge. Historical
observations below are attributed to the repository's earlier audits. Passing
these tests is not an in-game acceptance result.

## Mother and father: the clock is already implemented

[`character_sleep_work.py`](../tools/character_sleep_work.py) already limits
ordinary converter-owned work to the complement of the sleep intervals and
sets `HaltOnInvalid=true` on bounded work and sleep links. The sleep-pool choice
has priority 2. [`home_bed_assignment.py`](../tools/home_bed_assignment.py) gives
assigned beds priority 100 and moves the sleep-pool spatial anchor to the
preferred bed. Quest activities remain eligible to override ordinary routines.

The [earlier installed-data audit](parent-sleep-audit.md) recorded Father's sleep
window as 20:13–06:46 and Mother's as 22:12–06:52. Those are the recorded package's windows;
this investigation did not reconstruct them from new game data. Adding another
bedtime window or repeating the pool-anchor/work-cutoff changes would duplicate
current code.

[`character_sleep_native.py`](../tools/character_sleep_native.py) validates the
native bed's exclusive `use` reservation, the `lying` stance, the scoped
`sleeping` context and its repeating behavior. These checks establish the
expected exported contract. They cannot show whether a particular NPC selects
it, successfully reserves the bed, reaches its approach or enters the stance.

### Two different spatial problems

**The route upstairs remains incomplete.**
[`forge_loft.py`](../tools/forge_loft.py) clones a player-usable ladder and adds
its animation registration, but does not author a navigation connection for its
new identity. The prior audit found no reference to this ladder in the installed
archive's 362 mesh pairs. That count is historical evidence, not a fresh scan
performed here. Landing coverage and the upper floor's connections also need
validation. The player being able to climb or sleep does not prove an autonomous
NPC route.

There is also an older, separate conversion gap:
[`ladders.py`](../tools/ladders.py) gives imported ladders new GUIDs, while
[`region_travel_navigation.py`](../tools/region_travel_navigation.py) writes the
original navigation payloads unchanged. Static-profile composition checks door
references, not ladder references. The prior audit additionally found KCD1
ladder category 2 versus native KCD2 category 3. The new diagnostic derives the
native category from the provided target installation instead of assuming it.
Repairing existing source references would not create the newly authored loft
route.

**Bed scheduler positions are a separate converter defect.** Loft conversion
copied the native terminal bed hub, changed its identity and retained any
`Position` supplied by its donor. This differs from the sleep-pool position
already corrected on main. The branch removes the donor hub's `Position`,
matching [`world_beds.py`](../tools/world_beds.py) and
[`room_rental.py`](../tools/room_rental.py), so the placed bed supplies its
location. It does this for new beds and existing marked lofts and validates the
result. Healing effects, bed assignments, clocks and unrelated hubs are retained.

The regression fixture proves that the old converter propagated a stale donor
position. It does not prove that the donor in the user's exact installed build
contains that attribute. Father reaching upstairs but failing to lie down still
needs separate bed-approach, reservation and stance evidence.

## Horse hand: behavior and world graph can become different versions

The current [`character_horse_handler_work.py`](../tools/character_horse_handler_work.py)
behavior performs these steps under its pitchfork hand-content scope:

1. Resolve the cart's `stableAnimationStart` link.
2. Approach the separate start point with `ExactMove`.
3. Play `HayUnloadWagonIn` aligned to the cart frame.
4. Run the `HayUnloadWagon` loop with the selected native tags.
5. Play `HayUnloadWagonOut`.

The adapter already computes that start point from the source cart helper and
the target action table's `init_align0` transform. The behavior's alignment
object remains the cart frame; the entry point and alignment frame are different
poses. Its prior alignment/tag correction is described in
[the horse-handler notes](horse-handler-upgrade.md).

The upgrade defect is the path taken by **an already converted job**.
[`baseline_schedule_names()`](../tools/character_schedule_world.py) accepts only
the importer's idle `schedulerWait` schedule. The horse hand's converted job no
longer matches it. However,
[`job_resources()`](../tools/character_schedule_jobs.py) publishes the latest hay
behavior whenever it finds the hay template in the package. An older cart can
therefore receive a mandatory graph search for an approach link that its world
data never created. A behavior or fragment being present in the PAK is not enough
to make that package consistent.

The branch adds an explicit existing-job migration before resource publication
in both the population upgrader and the standalone person-schedule builder. It
runs before new jobs are bound, so a new worker can safely share an older cart.
It derives the approach from the retained cart alignment and validated native
entry animation, repairs the owned graph, and retains the actor's work/sleep
clocks and tool reservation. Conflicting graphs are rejected. This addresses a
reproducible package-upgrade incompatibility; it does not establish that every
reported idle occurrence came from an older package.

Migration is deliberately limited to the known old cart-origin layout and the
current approach layout. Unknown offsets and historical mixed-case source GUID
hash inputs cannot be reconstructed reliably from the emitted lowercase name;
they fail explicitly instead of being reassigned. This is not a universal
migration for arbitrarily modified job graphs.

No speculative engine fix was made for the custom link-tag name, `LinkTagFilter`
output binding or the action tag syntax. The available code does not establish
those as the cause. A current-package failure must be located at the failed
search, move, entry action or loop rather than inferred from a visible idle pose.

## Inspect the installed navigation

Run from this branch's checkout with paths to the actual package and both games:

```powershell
python tools/audit_ladder_navigation.py `
  --package "D:\path\to\mod" `
  --source "D:\path\to\KingdomComeDeliverance" `
  --target "D:\path\to\KingdomComeDeliverance2" `
  --require-ladder gm_forge_loft_ladder `
  --output ladder-navigation.json
```

The tool reads the PAKs and writes only its JSON report. It examines every stored
mesh pair and distinguishes base references, references present only in a state
variant, and absent references. Inspect `requested_ladders`,
`unresolved_or_wrong_class_references` and `non_native_category_references`.
Malformed mesh pairs fail inspection instead of being silently ignored.

A matching reference is not a verified path. The report explicitly leaves
polygon connectivity, landing/bed approaches and runtime traversal unverified.

## Applying and validating the branch

The branch changes converter source. Pulling it does not update installed PAKs,
the setup launch copy or an NPC's saved execution state. The shared population
and loft finalizers must run again against a private build stage, followed by
the normal receipt/transactional installation workflow. This investigation does
not bump the recipe or setup release version; an already-current cached build
can skip conversion unless explicitly re-finalized. Do not treat a loose staged
overlay as a complete installable package.

For live acceptance, record the tested package hashes and actual in-game time:

1. Observe each parent's natural work-to-sleep transition. Distinguish work
   remaining selected from sleep being selected and then failing.
2. Record the selected bed, reservation result, route result and stance entry.
   Test a valid upstairs approach independently from the complete downstairs
   route. A forced animation is only a diagnostic, not scheduling acceptance.
3. Confirm wake-up, work resumption and save/reload after a successful natural
   transition.
4. For the horse hand, confirm the installed approach point/link and current
   native entry transform, then observe tool pickup, approach, entry, visible
   hay loop and exit. Capture the first failed operation if he stops.
5. Check interruption/tool return and the job-to-sleep transition separately.

The remaining full stable routine—water, grain, horse care and the hay visual—is
still outside this hay-unloading adapter's implemented scope.

## Source validation performed

The consolidated focused suite ran **128 tests: 126 passed and 2 skipped** under
Python 3.12.14. The two skipped room-layout tests require Lupa, which was not
installed. No changed Lua execution path depended on those skipped checks.
`python -m compileall -q tools`, both affected command-line help entry points,
and `git diff --check` also passed.

The focused suite covered loft conversion/repair, sleep/work clocks, bed and
room contracts, navigation parsing and audit, hay migration, schedule binding,
tool reservations, population upgrading and resource publication. In particular:

- The new loft tests reproduced the donor-position bug on baseline, then passed
  with the correction. They cover new beds, marked-layout repair, preservation
  of unrelated hubs/healing/clocks and repeated conversion.
- Seven navigation-audit tests cover old source identities, category discovery,
  base versus variant-only links, missing/wrong-class/foreign references,
  malformed mesh variants and byte-for-byte preservation of input packages.
- Fifteen horse-hand tests include old-graph migration, current-binder output,
  rotated native entry poses, shared carts, repeat upgrades, preserved clocks
  and tools, conflict refusal, and actual population resource publication with
  the migrated graph. A conflicting graph leaves the package archives unchanged.

The exact consolidated command used in the source test environment was:

```bash
PYTHONPATH=tools:tests python -m unittest -q \
  test_forge_loft_scheduler test_character_sleep test_character_work_clock \
  test_bed_scheduler test_room_installation test_room_layout test_bed_alignment \
  test_world_beds test_audit_ladder_navigation test_static_navigation_profiles \
  test_campaign_navigation test_character_horse_handler_work \
  test_character_schedule_jobs test_character_schedule_world \
  test_profile_population_upgrade test_setup_profile_population \
  test_character_job_items test_npc_path_audit
```

The full game-dependent converter, Windows setup executable and native runtime
were not exercised. No installed game files or saves were modified.
