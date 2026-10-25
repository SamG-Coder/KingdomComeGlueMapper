# Runtime MCP acceptance setup

The Skalitz acceptance runner in this repository,
[tools/skalitz_mcp_tests.py](../tools/skalitz_mcp_tests.py), drives the game
through a local MCP client speaking the KCD2Mcp stdio protocol. The client is
built from the public, MIT-licensed
[SamG-Coder/KCD2Mcp](https://github.com/SamG-Coder/KCD2Mcp) repository; it lands
in `out/client/KCDTestMap.Client.exe` and is not shipped with this project.
Nothing is installed into the game: the client injects a native bridge into a
running game and exchanges JSON-RPC with it over a current-user named pipe.

This page documents the setup so other contributors can reproduce it, as
promised in [issue #35](https://github.com/SamG-Coder/KingdomComeGlueMapper/issues/35).
The build and registration steps follow the
[KCD2Mcp README](https://github.com/SamG-Coder/KCD2Mcp/blob/main/README.md); the
game-side answers come from contributor PhatBanana's
[issue #35 answer](https://github.com/SamG-Coder/KingdomComeGlueMapper/issues/35#issuecomment-6322939098).
Paths such as `D:/KCDTestMap` and `D:/SteamLibrary` are the maintainer's checkout
and library locations; substitute your own. This page records the setup, not
in-game results: where a check has not been run, or a tool is not public, it
says so.

**Public-client compatibility, checked 26 October 2026:** the published
[client at `50b2712`](https://github.com/SamG-Coder/KCD2Mcp/blob/50b2712eae95683068ee3ad99b91b300f335eb2c/src/Client/Program.cs)
has 11 tools. It supports its own lifecycle test and direct inspection, but
lacks `player_state`, `inspect_npc` and `list_entities`, so it cannot run
GlueMapper's `observe` mode. It also lacks the `native_menu_*` and
`henry_task_*` tools required for `exercise`. Building that public revision
does not add those capabilities. Use `plan` offline, or use a separately
provided compatible client whose `tools/list` includes every required tool.
The maintainer's local checkout may contain unpublished extensions; it is not
the public release contract.

## Build the client

Clone [SamG-Coder/KCD2Mcp](https://github.com/SamG-Coder/KCD2Mcp) (MIT)
anywhere outside the game folders. Requirements, from the
[KCD2Mcp README](https://github.com/SamG-Coder/KCD2Mcp/blob/main/README.md):

| Requirement | Used for |
| --- | --- |
| Windows x64 | The client and the injected plugin are Windows binaries |
| .NET 10 SDK | Builds the C# NativeAOT client and plugin |
| Visual Studio 2026 C++ tools | Compiles the native bridge parts |
| CMake | Native build configuration |
| Git | Cloning the repository |

Then build:

```powershell
git clone https://github.com/SamG-Coder/KCD2Mcp.git
cd KCD2Mcp
./build.ps1
```

`build.ps1` fetches the pinned MinHook dependency when it is absent. The client
lands in `out/client/KCDTestMap.Client.exe`. Close the game before rebuilding
the loaded plugin DLLs.

The runner in this repository defaults to
`D:/KCDTestMap/out/client/KCDTestMap.Client.exe`; that is only the maintainer's
checkout path, so always pass your own build path with `--mcp` (see
[Use it from GlueMapper](#use-it-from-gluemapper)).

## Register the server

The same executable offers three entry points.

**Codex.** Register the client as a stdio MCP server:

```powershell
codex mcp add KCD2Mcp -- C:\KCD2Mcp\out\client\KCDTestMap.Client.exe
codex mcp get KCD2Mcp
```

If a client's tool list does not refresh, open a new session.

**mcp.json.** The repository's
[`mcp.json`](https://github.com/SamG-Coder/KCD2Mcp/blob/main/mcp.json) holds the
equivalent stdio configuration for other MCP clients (`command` with empty
`args`). Its recorded path is the maintainer's; replace it with your own build
path.

**CLI adapter.** The client also accepts ordinary newline-delimited JSON-RPC
over stdin/stdout; stdout is reserved for the protocol and diagnostics go to
stderr. A command-line adapter uses the same implementation without an MCP
client:

```powershell
& C:\KCD2Mcp\out\client\KCDTestMap.Client.exe call runtime_status
& C:\KCD2Mcp\out\client\KCDTestMap.Client.exe call inspect_world
```

## Run against the game

- Nothing is installed into the game. `launch_game` starts Steam app 1771300
  **without custom game arguments** and injects a native bridge that hosts the
  C# NativeAOT plugin; requests reach C# over a current-user named pipe, and
  engine operations run on the engine update thread. Steam must already be
  signed in and the game installed; unrelated Steam update or login dialogs are
  not handled by the plugin.
- `attach_plugin` attaches to an already running game, but early attachment
  during launch is preferable because the bridge captures the engine's Lua
  state at startup.
- The bridge permits only the audited retail `WHGame.dll` it was built against
  and requires unique native function signatures. Other game builds are
  refused, so a game update needs a compatibility audit and matching client and
  plugin changes; rebuilding the same sources alone does not add support.
  Restart the game
  to load an updated plugin; live unload is not implemented.
- **Back up `Saved Games/kingdomcome2/saves` before any test session.** The
  plugin never modifies saves automatically, and the KCD2Mcp lifecycle test
  does not load or overwrite saves, but world tools such as `load_trosky`
  replace the loaded world and can discard unsaved progress.

The README's unattended workflow is `launch_game`, `runtime_status`,
`load_trosky`, `inspect_world`, then the content tools (`create_demo`,
`inspect_entity`, `test_mechanics`, `read_logs`, `remove_demo`). A load timeout
after execution begins means inspect before retrying, not that loading failed.
`execute_lua` provides live engine inspection and operations with up to 32 KiB
of code; return small JSON-compatible values without cycles, because the output
transport uses a game log line and large results can be truncated.

## Use it from GlueMapper

[tools/skalitz_mcp_tests.py](../tools/skalitz_mcp_tests.py) is the
source-driven Skalitz evidence runner. It starts the client as a subprocess and
speaks real MCP JSON-RPC over stdio to it, with deadlines and captured stderr.
With a compatible client and the converted destination already loaded:

```powershell
.venv/Scripts/python.exe tools/skalitz_mcp_tests.py --mcp C:\KCD2Mcp\out\client\KCDTestMap.Client.exe --mode observe
```

Defaults and overrides:

- `--mcp` defaults to the maintainer's `D:/KCDTestMap` path; always override it.
- The manifest defaults to `outputs/CurrentBuild/skalitz-acceptance.json`, the
  quest IR to
  `outputs/BuildWork/skalitz-campaign-graph-adapter/source-ir/q_skalitz.json`
  and the spec to [docs/kcd1-campaign-start-spec.md](kcd1-campaign-start-spec.md).
  Override with `--manifest`, `--quest-ir` and `--spec`; `--game-log` selects
  the game log to capture.
- `--mode plan` builds the test plan without contacting the game. It still
  requires a generated acceptance manifest; the default paths are build
  outputs, not files included in a fresh checkout.

Modes:

- `--mode observe` needs no ephemeral session and only reads the active world.
  It requires the read tools `runtime_status`, `player_state`, `inspect_npc`
  and `list_entities`, checks that the active world matches the manifest's
  destination level, inspects a bounded set of nearby source entities, and runs
  a read-only quest probe through `execute_lua` when that tool is available.
  `execute_lua`, `inspect_entity` and `inspect_world` work against any loaded
  world; the full observe run is bound to the Skalitz destination level from
  the manifest. With the published 11-tool client, the report instead records
  `run_boundary: blocked` and lists the missing inspection tools.
- `--mode exercise` adds one bounded movement test
  (`henry_task_start`/`henry_task_status`) and requires a prepared ephemeral
  native-menu session passed with `--session` (32 hex digits). It verifies that
  session with `native_menu_*` tools such as `native_menu_status` and
  `native_menu_verify_saves`. These tools, and the Henry movement tools, are
  absent from the public revision checked above. A compatible extended client
  and prepared session are required.

Each run writes `plan.json`, `report.json`, per-call JSON under `evidence/`,
the MCP stderr and the game-log interval to the output directory (default
`outputs/CurrentBuild/mcp-tests/<timestamp>-<id>`; override with `--output`).
Observations are recorded as evidence, not as gameplay passes: the report
always leaves acceptance cases `incomplete` and `fully_verified` false.
Registration, capability probes and dispatch acknowledgements never pass an
entire gameplay acceptance family.

## Verify it works

The checks below cover different capabilities; passing the public client's
lifecycle test does not establish compatibility with GlueMapper's runner:

1. **KCD2Mcp's own lifecycle test.** With a test Trosky world loaded, run
   `python test_mcp.py` in the KCD2Mcp checkout
   ([`test_mcp.py`](https://github.com/SamG-Coder/KCD2Mcp/blob/main/test_mcp.py)).
   It exercises actual MCP initialize/list/call messages, creates six props
   near Henry, inspects each, rejects duplicates, removes them, verifies their
   absence and recreates them. Evidence is written under
   `runtime/mcp-lifecycle-test.json`. The test does not load or overwrite
   saves.
2. **A short observe run from this repository**, only with a compatible client,
   a generated acceptance manifest, and the manifest's converted destination
   loaded. The native Trosky world from the first check is a different world:

   ```powershell
   .venv/Scripts/python.exe tools/skalitz_mcp_tests.py --mcp C:\KCD2Mcp\out\client\KCDTestMap.Client.exe --mode observe --output outputs\CurrentBuild\mcp-tests\observe-smoke
   ```

   `report.json` should record `runtime_status`, `player_state` and
   `list_entities` observations and still report `fully_verified: false`; that
   is expected, because observation is evidence rather than acceptance.

The runtime setup does not substitute for in-game validation (for example the
issue #35 sit/eat retest). If a change cannot be verified without the games,
state that plainly in the pull request; see
[CONTRIBUTING.md](../CONTRIBUTING.md).
