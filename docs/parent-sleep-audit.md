# Parent sleep audit — 22 October 2026

The [23 October source investigation](parent-sleep-horse-hand-investigation.md)
adds a reproducible ladder-reference audit and a separate correction for copied
bed scheduler positions. The unresolved autonomous loft route remains distinct
from those changes.

## Result and scope

The installed loft has player-usable beds, but its NPC navigation is incomplete.
Henry can sleep in all three beds, as confirmed by the user. This does not prove
the autonomous NPC route, reservation or approach succeeds. The game was closed
during this audit, so no new live AI decision trace was available.

Read-only evidence was collected from the installed retail mod, its setup launch
copy, KCD1 source archives, and KCD2's exported Trosky XML and navigation data.
No installed archives were changed. Detailed local evidence is in
`reports/parent-sleep-audit-20261022.json`; the repeatable audit script is
`reports/audit-parent-sleep.py`.

## Confirmed navigation defects

1. The loft ladder has no navigation connection in any of the installed
   navigation archive's 362 mesh pairs. Its GUID is
   `c9ef1513-ef3d-d45f`. Interactive registration is present, but the loft
   converter does not build or update a navigation archive. Its validation
   checks that the ladder is usable, not that an NPC can traverse it.
2. The installed base mesh contains 100 ladder connections still using KCD1
   category 2 and original source entity GUIDs. All 100 resolve to source
   Ladder entities; none resolve to the installed converted entities. Of
   these, 100 connections correspond to 99 converted ladder entities.
3. Native Trosky's base mesh uses category 3 for its 58 ladders, and all 58
   connection GUIDs resolve to native Ladder entities. The converter gives
   imported ladders new GUIDs while the navigation packer preserves source
   tile contents. Matching Recast version/settings does not validate these
   category and entity references.

The generic imported-ladder issue and the new loft ladder require different
repairs: remap existing authored connections, and author a new validated loft
connection. Remapping imported links alone cannot create the loft route.
The landing, upper-floor navigation and bed approach surfaces must also be
checked; this audit does not certify their polygon coverage or connectivity.

## Schedules and assignment

| Actor | Installed work window | Installed sleep window |
| --- | --- | --- |
| Mother | 06:52–22:12 | 22:12–06:52 |
| Father | 06:46–20:13 | 20:13–06:46 |

Both actor roots contain the timed sleep pool at priority 2. Each pool contains
the actor's own bed at priority 100 during both nighttime interval segments,
alongside lower-priority fallback beds. Each assignment has matching
`sleepBed` and reciprocal `preferedSleeper` links. Both beds have the native
`so_bed` type and high-bed helper collection reference. The native bed resource
allows one occupant; no missing bed behavior definition was found.

Both installed pool positions still point at `(875.03058,3345.5144,25.711576)`
rather than their loft beds near `(735–737,3422.8,67.315)`. This is stale spatial
metadata. A correction exists in the staged shared upgrader, but its actual
effect on selection has not been demonstrated. A hub position is not evidence
that an NPC was explicitly ordered to walk to that exact point.

The installed work links use `HaltOnInvalid=false`. This is not independently
proof of a fault: native Trosky has 1,192 timed links with false and 10 with true.
The staged change to true is a candidate for enforcing the invented work
cutoff, not a verified explanation of Mother's nighttime sweeping.

## Excluded explanations and remaining uncertainty

- The sleep windows include midnight; this is not merely a missing bedtime.
- Level-local and mod-prefixed scheduler/animation tables agree.
- The setup launch copy matches retail for mission entities, scheduler data,
  and the base navigation mesh. A stale launch copy was not found.
- Henry's successful sleep rules out a blanket failure of all bed interactions.
  Native NPC `use` and player `playerAction_stanceObject` are distinct behaviors,
  even though they share the bed resource.
- The earlier observation of Father upstairs but failing to lie down is not
  fully explained by a missing ladder connection. Bed approach navigation,
  reservation/stance rejection, and how he reached that position remain open.
- Mother's observed continued work could involve an active behavior that did
  not yield, a failed sleep selection, or another execution-state issue.
  Static XML cannot distinguish these without a live decision/failure trace.

## Repair and acceptance order

1. Add generic validation and conversion of ladder category/identity references.
   Preserve authored endpoint geometry; reject unresolved identities.
2. Supply and validate the new loft route, including both ladder landings and
   upper-floor connections. Do not treat player traversal as NPC validation.
3. Test native NPC bed use from a valid upstairs approach, separately from the
   full downstairs-to-bed route, to isolate Father's entry failure.
4. Observe the natural work-to-sleep transition, recording failed selections,
   reservations and stance attempts. Evaluate the staged cancellation change
   against that evidence rather than assuming it is necessary.
5. Verify both assigned beds, wake-up, normal work resumption and save/reload.

This audit identifies concrete conversion gaps; it does not claim that the
parents' sleep behavior is fixed or that every observed symptom has one cause.

## Offline code changes — 23 October 2026

The shared generated schedule now expires its sleep links at wake-up, as well
as clipping imported work to waking hours. Migration accepts only the known
old halt-policy default while preserving the generated clock and bed bindings.
The parent pool anchor follows its assigned loft bed. In a staged package, the
assignments and mounted scheduler copies agree: Mother 22:12–06:52, Father
20:13–06:46.

These changes do **not** complete the navigation repair above. The current loft
builder still emits an interactive ladder without an authored NPC connection;
its added upper-floor landing and bed approaches are not certified by a navmesh
bake. A fresh scan of the installed archive on 23 October again found zero
connections to the loft ladder across all 362 mesh pairs. The local SDK includes
editor navigation facilities, but no tested offline
bake adapter or editable converted travel level was found in this audit.
Do not label the parents' autonomous sleep fixed based on schedule XML, player
bed use, or the automated test suite. Their complete route and natural bedtime
transition remain outstanding.
