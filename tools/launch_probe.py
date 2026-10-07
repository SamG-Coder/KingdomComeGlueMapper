"""Launch an isolated development level and place the player above its terrain."""
import argparse
import math
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def console(command):
    url = "http://localhost:1403/api/System/Console/ExecuteString?"
    with urllib.request.urlopen(url + urllib.parse.urlencode({"command": command}), timeout=5) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", default="kcd1_terrain_v6")
    parser.add_argument("--tools", type=Path, default=Path(r"D:\SteamLibrary\steamapps\common\KCD2Mod"))
    # Original q_skalitz_questitems / TagPoint232, next to the opening
    # mother/Henry dialogue cameras. Height is queried from converted terrain.
    parser.add_argument("--x", type=float, default=734.89313)
    parser.add_argument("--y", type=float, default=3421.4497)
    parser.add_argument("--clearance", type=float, default=1.0)
    parser.add_argument("--walk", action="store_true", help="Disable default no-collision flight for traversal tests")
    parser.add_argument("--attach", action="store_true", help="Only fix spawn in the already running specified level")
    args = parser.parse_args()
    if not args.level.replace("_", "").isalnum():
        parser.error("Use an alphanumeric level name with optional underscores")
    if not all(math.isfinite(v) for v in (args.x, args.y, args.clearance)) or not (0 < args.x < 4096 and 0 < args.y < 4096 and 0.2 <= args.clearance <= 5):
        parser.error("Coordinates must be inside the 4096m terrain; clearance must be 0.2 to 5m")
    if not (args.tools / "Data/Levels" / args.level / "terrain.pak").is_file():
        parser.error("Compiled terrain package is missing")
    log = args.tools / "kcd.log"
    if not args.attach:
        # Do not close another game or create a duplicate instance.
        running = subprocess.run(["powershell", "-NoProfile", "-Command", "if (Get-Process KingdomCome -ErrorAction SilentlyContinue) { exit 1 }"], capture_output=True)
        if running.returncode:
            parser.error("Kingdom Come is already running. Close it, or use --attach with its current level")
        executable = args.tools / "Bin/Win64ReleaseSteamLTO_DLL/KingdomCome.exe"
        process = subprocess.Popen([str(executable), "-devmode", "+g_skipIntro", "1",
                                    "+sys_intromoviesduringinit", "0",
                                    "+wh_sys_SkipStartupVideoWhenPossible", "1",
                                    "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720",
                                    "+map", args.level], cwd=args.tools)
        print(f"Launched {args.level}, PID {process.pid}; waiting for level and player", flush=True)
    else:
        process = None
    marker = "GLUEMAPPER_SPAWN_" + uuid.uuid4().hex
    fly_mode = "FlyMode_Off" if args.walk else "FlyMode_OnNoCollisions"
    command = ("#if player and player.player then local z=System.GetTerrainElevation({x="
               + str(args.x) + ",y=" + str(args.y) + ",z=0}); "
               + "if z==z and z>-10000 and z<10000 then player.player:SetFlyMode(" + fly_mode
               + "); System.ExecuteCommand(\"goto "
               + str(args.x) + " " + str(args.y) + " \"..tostring(z+" + str(args.clearance)
               + ")); System.LogAlways(\"" + marker + " ground=\"..tostring(z)..\" fly=\"..tostring(player.player:GetFlyMode())); end; end")
    deadline = time.monotonic() + 180
    next_attempt = 0
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise SystemExit(f"Game process exited with code {process.returncode}")
        contents = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        if marker in contents:
            print(next(line for line in contents.splitlines() if marker in line), flush=True)
            print("Terrain-based spawn command executed. Check movement and collision in game.", flush=True)
            return
        # Use the latest completed level, not a stale earlier entry in this log.
        loaded = [line for line in contents.splitlines() if "*LOADING: Level " in line and " loading time:" in line]
        ready = bool(loaded) and f"Level {args.level} loading time:" in loaded[-1]
        if ready and time.monotonic() >= next_attempt:
            try:
                console(command)
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            next_attempt = time.monotonic() + 3
        time.sleep(0.5)
    raise SystemExit("Spawn not confirmed within 180 seconds; game left running. Check kcd.log.")


if __name__ == "__main__":
    main()
