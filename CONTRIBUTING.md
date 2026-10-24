# Contributing

KingdomComeGlueMapper is developed on Windows and converts assets from your own
installed copies of KCD1 and KCD2. **No game archives, extracted assets or
generated packages are accepted in a pull request.**

## Set up a development environment

The converter is a set of Python modules in [tools](tools). Python 3.13 and
3.14 are tested on Windows x64. Python 3.13 is the easiest starting point for
the pinned native dependencies:

| Python | Dependency setup | Local test result |
|---|---|---|
| 3.13 | xatlas 0.0.11 provides a Windows wheel | full suite passed |
| 3.14 | xatlas 0.0.11 must build from source using Visual Studio C++ build tools | full suite passed with dependencies installed |

The missing cp314 wheel is a packaging limitation, not a Python 3.14
incompatibility. The parallel CPU baker supports both versions. Other native
dependencies still need compatible wheels or build tools. A failed pip install
can leave a partially prepared environment; fix the reported dependency and
rerun the requirements command before testing.

Use Python 3.13 to avoid compiling xatlas:

```powershell
git clone https://github.com/SamG-Coder/KingdomComeGlueMapper.git
cd KingdomComeGlueMapper
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-setup.txt
.venv/Scripts/python.exe tools/setup_app.py
```

For the test suite:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

`requirements-test.txt` adds Lupa (executable Lua checks) and xatlas on top of
`requirements-npc-effects.txt` and `requirements-lighting.txt`. The synthetic tests run without the games or Modding Tools. Tests requiring
optional GPU binaries or local game-derived fixtures report skips when those
resources are absent; a green synthetic suite does not verify gameplay.

## Before opening a pull request

Run these checks locally. Automatic Actions runs are disabled; the manually dispatched workflow in
[validate.yml](.github/workflows/validate.yml) runs the same core checks. It also
invokes `--help` on a fixed list of command-line entry points; the last command
here stands in for that sweep:

```powershell
.venv/Scripts/python.exe -m compileall -q tools
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe tools/compare_packages.py --help
```

The following deviations are expected on a machine with no game assets and
report as skips, not failures: missing CUDA/OptiX DLLs, and the game-asset
room-rental fixtures.

A focused run is much faster while iterating:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_brush_registration.py -v
```

When an optional native backend is genuinely absent, skip the test instead of
letting it error, so that a missing extra cannot be mistaken for a regression:

```python
try:
    import optional_backend
except ImportError:
    self.skipTest('optional backend not installed')
```

## Tests

Every conversion rule that can be checked without the games belongs in
[tests](tests). The suite builds synthetic archives and meshes in memory, so
new synthetic tests must not require a game installation. Keep optional
game-dependent checks explicitly skippable. Use temporary directories instead of
`outputs/` or `research/`. Follow the existing style: one `unittest` module per
converter, imports taken from `tools/`.

### Runtime MCP acceptance (optional)

The runtime evidence runner `tools/skalitz_mcp_tests.py` needs the games and a
locally built KCD2Mcp client. See the
[runtime MCP acceptance setup](docs/runtime-mcp-setup.md) guide. Runtime checks
are optional for pull requests; state plainly what you could not verify without
the games.

## Reporting an issue

Include, as far as you can:

- converter commit, release or setup version
- game version and which game the asset came from
- the in-game location, or coordinates
- whether the problem is appearance, collision, interaction or persistence
- reproduction steps and a screenshot
- relevant `setup.log` excerpts, or a report file from `outputs/`

Do not attach game archives or extracted game content.

## Pull requests

Keep a pull request to one concern. Say what you changed, why, and how you
verified it. If a change cannot be verified without the games, state that
plainly rather than implying a runtime fix; the maintainer validates appearance
and gameplay in retail. Mention the exact commands you ran and their result.

Documentation-only and test-only pull requests are welcome.

See the [Windows setup guide](docs/windows-setup.md#build-from-source) for
packaging and frozen-runtime checks; source tests alone do not validate the
distributed executable.
