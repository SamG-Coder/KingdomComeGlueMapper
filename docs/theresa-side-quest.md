# Visit Theresa (issue 9)

The travel importer now registers **Visit Theresa** as a native **Side** quest
under the KCD1 destination Level. It starts on arrival only while its persistent
progress is None. Talking to the imported Theresa completes this visit objective;
an area trigger, proximity check or console command cannot complete it.

The original `q_romanceWithTheresa` controller's `QuestActor[('tereza')]` link
selects the person. The shared person/appearance helpers import her source soul,
instance, mill position, head, body and outfit. Her localized name is read from
KCD1 and given a separate target key. Storm rules merge with existing imported
people. The arrival-quest helper accepts a specification so a second quest does
not duplicate arrival ports, objective type definitions or the player asset.
Rattay's saved-state paths, quest XML, journal XML and dialogue are preserved.

## Date scope is still open

This change registers the visit and character, **not a playable date**. Theresa's
current menu only has End dialog after the visit event. There are no fabricated
romance responses, and no date success is emitted by this visit quest.

The installed source has two distinct systems:

- `q_millerDate`: the original Courtship dates, including walk-and-talk behavior.
- `q_romanceWithTheresa`: later repeatable outings. These use StartFastTravel,
  cast/teleport markers, conditional dialogue, dynamic daycycle patches and a
  return-home handoff. They are not just a list of walking waypoints.

`travel-build.json` records the authored places, script provenance and behavior
tree names under `theresa.date_source_audit`. The beer outing dialogue also
requires objective conditions, answer counters and an item-grant event; the
existing dialogue converter rejects those unsupported actions. Full date work
must translate those dependencies, original voice/presentation resources and
native movement/scene behavior, then test repetition and save/load in retail.
The imported character currently has no converted daily activity schedule.

## Validation and retail test

- 276 automated tests pass, including two independent arrival quests, Side
  classification, region/marker binding, conversation completion and duplicate
  registration rejection.
- The complete travel build succeeds against installed game archives.
- All 14 generated tables pass the official GeneratedDatabase.dll reader.
- Generated file hashes, PAK CRCs and scheduler target references pass.
- The Rattay quest, journal and innkeeper dialogue are byte-identical to the
  installed working version. The new character resources are additive.

Retail acceptance is pending. Enter the KCD1 region through the coachman, check
**Side** in the journal and the marker at Rattay mill, talk to Theresa, then
revisit/reload to confirm the completed visit does not restart. Check the existing
Rattay innkeeper's Talk/Trade flow as a regression test. No full date, movement,
voice playback or daily schedule is claimed by these package checks.
