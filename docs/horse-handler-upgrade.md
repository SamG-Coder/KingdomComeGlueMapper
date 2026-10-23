# Source-linked horse-handler work

The general population upgrader recognizes
`final/sa_stable.xml:horseHandler_work`. The first supported phase is hay-cart
unloading. It selects the workplace and pitchfork from the source activity's
linked stable resources; actor names and settlement names do not select the
conversion. The desktop setup converter inventory includes the adapter.

The converted schedule retains its work intervals and existing sleep windows.
An actor-specific schedule hub leads to one exclusive resource per source cart.
That resource reserves the authored pitchfork through the native item-slot
graph. Repeated work intervals reuse the same hub and tool. The behavior holds
the tool, walks to the native entry animation's start pose, and requests KCD2's retained
`HayUnloadWagonIn`, `HayUnloadWagon` and `HayUnloadWagonOut` fragments.
The package also registers `stable_pitchfork` in an additive decision-label
table. Publishing a behavior and reservation edge without that registration
leaves the native tool decision unresolved.

Conversion validates the source behavior search, cart and pitchfork identity,
item registration, target animation fragments and their matching executable action rows. It rejects conflicting
anchors and missing dependencies instead of silently substituting props.
The currently validated animation contract is male-only; female handlers are
explicitly unsupported pending a compatible target animation contract.

This is a partial horse-handler routine. Water refill, grain feeding, horse
assignment/care and the temporary hay visual remain explicitly pending in the
build report. The source stable must have one unambiguous linked hay cart and
pitchfork for this adapter. Supporting several carts requires an additional
selection policy.

## Validation

Regression tests cover behavior selection, changed source searches, repeated
intervals, conflicting resources, missing tool registrations, native fragment
availability and the approach-before-animation sequence. Package validation
checks scheduler targets, mounted aliases, archive CRCs, preserved sleep
windows and unchanged unrelated NPCs/schedules.

The bounded Skalitz candidate is installed for `ska_groom`. Live acceptance
requires observing autonomous tool pickup, cart approach, the unloading
animation, and the work-to-sleep transition. Package checks alone do not count
as runtime verification.

## Animation alignment correction

The first candidate conflated the cart alignment frame and the actor's approach
position. An attempted tool-slot offset also ignored the animation transform.
The shipped `Libs/Tables/action/actor_anim_action.xml` records
`HayUnloadWagonIn` with `init_align0=0.053912;3.242079;0;0;0;0;1`.
Walking to the alignment object's origin therefore does not reach the exported
entry pose. The converter now composes the native start transform with the
source cart helper, registers a separate approach point, and retains the source
alignment frame for `AnimationAction`. The tool slot is used only for tool
reservation, not to infer animation geometry. No NPC coordinates or guessed
clearance distances select the approach.

Mannequin's `left+up` variant is emitted as `left,up` for the behavior action,
matching native behavior tag syntax. Entry, loop and exit must have matching
fragment GUIDs, executable NPC action rows, compatible controller IDs and
one-shot/loop lifecycles. Missing or ambiguous data rejects conversion.

The preceding installed candidate picked up its pitchfork and approached the
cart, but the user confirmed that it stood still. The alignment/tag correction
is installed and passes offline checks; it has not been verified in play. Do not mark this job runtime-verified on the strength of these checks.

## Existing-package migration investigation

The [23 October source investigation](parent-sleep-horse-hand-investigation.md)
identifies an upgrade path that can publish the current hay behavior while
retaining an older cart graph without its required animation-start link. The
investigation branch adds an owned-job migration before resource publication.
This is a package-consistency correction; the native animation and complete
autonomous work cycle still require live acceptance.
