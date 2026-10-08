# Windows setup application

Download the Windows x64 **KingdomComeGlueMapper Setup** executable from
[GitHub Releases](https://github.com/SamG-Coder/KingdomComeGlueMapper/releases).
Python and the modding tools are not required on the user's machine.

This is an **experimental world-import release**, tested against retail KCD2
**1.5.6**. It installs the converted KCD1 map and the **Play KDC1** New Game entry.
It does not yet implement original quests, the KCD1 intro, complete gameplay,
or validated campaign save/load. Existing KCD1 saves are not converted.

## Install

1. Install both legal copies of the games through Steam.
2. Run the setup executable. It reads Steam's library and app manifests to find
   both installations, including libraries on different drives. Use **Browse**
   if a game was not found or to select a different installation.
3. Choose a **Build folder** outside both game directories. Allow at least
   **60 GiB free** for the build. Installation also needs about **25 GiB free**
   on the KCD2 drive for staging. On the same drive these estimates are combined.
   These are conservative preflight estimates, not the final package size.
4. Close either running Kingdom Come game, choose **Build and install**, then
   select **Check locations** and **Build and install**.
5. Watch the stage progress, current operation and live log. Stages differ in
   duration: the overall bar counts stages, not an estimated time percentage.
   File packaging/copy/verification report measured progress; other stages show
   activity. A full conversion can take a while and creates many files.
6. When setup completes, launch KCD2 normally and choose **Play KDC1 → Standard
   Mode**. The original `spawnStart` places Henry beside his home in Skalitz.

The executable is unsigned. Setup requests no administrator elevation; the
selected folders must be writable by the current Windows user.

## Other operations

- **Build package only** converts and verifies everything without changing the
  installed mod. It can run while the game is open. The resulting `package`
  folder can later be selected with **Install existing package**.
- **Install existing package** verifies a previously generated campaign bundle
  and installs it without repeating conversion. Both game locations are still
  checked. A loader-only probe is not accepted as a campaign package.
- **Cancel after current stage** waits for a conversion stage to finish. During
  file copying, cancellation stops before replacement of the installed mod.
  The saved build output and log remain available for diagnosis.
- **Open log** opens `setup.log`; `events.jsonl` retains structured progress and
  errors. **Open output folder** opens the current run's directory.

Build output is retained rather than automatically deleting large directories.
Each run uses a unique folder. The current release starts a fresh build when
retrying a failed conversion; it does not claim checkpoint resume support.

## Installation and rollback

The destination is `<KCD2>/Mods/kingdomcomegluemapper`. Setup stages and verifies
the new files before replacing that one owned mod. An existing installation
must match its receipt; unknown or locally modified files block replacement.
The previous installation is moved into `<KCD2>/GlueMapper Backups/<run-id>`.
If the final rename fails, setup restores the old installation automatically.

Other mods, game archives and saves are preserved. If `Mods/mod_order.txt`
exists, it must already include `kingdomcomegluemapper`; setup reports a missing
entry rather than replacing another mod's load-order configuration.

For manual rollback, close the game, move the current owned mod somewhere safe,
then move the chosen backup back to `Mods/kingdomcomegluemapper`.

## Conversion stages

The app orchestrates the existing converters with separate source, target and
workspace paths. It does not need symlinks, an installed editor or an editor SQL
database. The stages cover terrain/materials, trees and merged grass, water,
buildings, streams, props, remaining instances, roads/decals, selected landscape
geometry, door/object visuals and tree material compatibility. The latest road
buffer and terrain height corrections are in the shared converters.

It then packages the imported dependencies into retail-sized archives, imports
the source spawn, registers the map, supplies the corrected database table mount
paths, and generates the menu patch from the user's own KCD2 UI. Original quest
files are retained as conversion inputs; they are not executable KCD2 quests.

All generated game content stays on the user's machine. The release executable
contains the converter, interface and Python/Tk runtime, not game assets.

## Build the executable from source

On Windows x64 with Python 3.14:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-setup.txt
.venv/Scripts/python.exe -m pip install -r requirements-npc-effects.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe tools/build_setup_exe.py
```

The build writes the EXE, `SHA256SUMS.txt` and frozen-runtime `self-test.json` to
`outputs/release`. The self-test checks bundled converter imports, Tcl startup
and the Lua resources. It does not run the games. The tag/dispatch GitHub Actions
workflow also builds and uploads these artifacts without access to game files.

The setup uses [PyInstaller](https://pyinstaller.org/en/stable/) to bundle the
runtime. Conversion remains subject to the limitations in the
[world import checklist](world-import-checklist.md).

## Release validation: 0.2.0-alpha.1

- The Windows executable's self-test passed: nine converter modules, Tcl/Tk,
  and both bundled Lua resources loaded from the frozen application.
- The visible application detected both local Steam games and correctly blocked
  installation when the destination drive lacked staging space.
- The frozen worker installed a synthetic package successfully. Automated tests
  cover replacement backups, rollback after a failed rename, cancellation,
  package tampering, separate Steam libraries and disk-space preflight.
- A fresh source-backend conversion from the installed games completed all 12
  conversion stages and package verification in approximately 19 minutes on the
  development machine. It produced 79,301 imported assets and a 15.7 GiB package,
  with 11,333 final visual/spawn entities and the original Skalitz spawn point.
- This newly generated package was not installed or replayed in retail because
  the game drive lacked staging space. Package verification is not an in-game
  acceptance test. Earlier retail world/menu validation is recorded separately.

The full test suite also uses `requirements-npc-effects.txt`; those dependencies
are excluded from this world-setup executable.
