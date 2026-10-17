"""Source-driven Skalitz evidence runner using the real KCD2Mcp stdio protocol.

Default mode builds a test plan without contacting the game. Observe mode only
reads the active world. Exercise mode adds one bounded movement test, requiring
an already prepared ephemeral native session with verified save protection.
Registration, capability probes and dispatch acknowledgements never pass an
entire gameplay acceptance family. No quest-start/completion or teleport calls
exist in this runner. Missing interaction adapters remain explicit work items.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any
import uuid

from upgrade_map import digest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "outputs/CurrentBuild/skalitz-acceptance.json"
DEFAULT_IR = ROOT / "outputs/BuildWork/skalitz-campaign-graph-adapter/source-ir/q_skalitz.json"
DEFAULT_SPEC = ROOT / "docs/kcd1-campaign-start-spec.md"
DEFAULT_MCP = Path("D:/KCDTestMap/out/client/KCDTestMap.Client.exe")
DEFAULT_GAME_LOG = Path("D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2/kcd.log")

# Coverage gaps describe the required observable contracts, not fake adapters.
MISSING_ADAPTERS = {
    "level_load": ["native lifecycle completion event", "body-rendering capture and visual assertion"],
    "opening": ["source-selected movie/cutscene observation", "dialogue choice through native UI", "exactly-once objective transition history"],
    "terrain_collision": ["authored doorway/road walking route", "ground-contact and blocking-collision observations"],
    "rendering": ["near/far image capture and expected asset visibility", "stream-out and return route"],
    "quest_triggers": ["verified native objective identity/state reader", "source branch routes", "exactly-once event observer"],
    "audio": ["actual audio/voice event capture", "subtitle-to-source matching", "normal cutscene finish/skip inputs"],
    "save_restore": ["isolated writable test-save lifecycle distinct from protected real saves", "process restart/Continue restore comparison"],
    "weather_time": ["native time/weather readback", "authored wait/time policy route"],
    "npc_ai": ["talk/provoke/flee/surrender interaction routes", "AI reaction and combat completion observations"],
    "crime_guards": ["witness/crime/guard state observation", "native surrender/punishment selection"],
    "doors": ["native aimed interaction input", "door animation/lock state reader", "doorway collision traversal"],
    "trade": ["shop/haggle UI choices", "source stock/money/ownership delta evaluator"],
    "horses": ["native call/mount/ride/dismount interaction route", "source ownership/tack identity comparison"],
    "beds": ["native bed and sleep UI interaction", "time/needs/crime/wake-position observations"],
    "inventory": ["native take/transfer/drop input route", "verified item GUID/quantity/ownership decoder"],
    "grindstone": ["native grindstone input route", "condition delta and control restoration assertions"],
    "dice": ["native dice UI interaction route", "stake/result/control restoration observations"],
}
REQUIRED_READ_TOOLS = {"runtime_status", "player_state", "inspect_npc", "list_entities"}
GAMEPLAY_TOOLS = {"henry_task_start", "henry_task_cancel"}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def point(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        result = {axis: float(value[axis]) for axis in ("x", "y", "z")}
    except (KeyError, ValueError, TypeError):
        return None
    return result if all(math.isfinite(v) for v in result.values()) else None


def level_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").strip("/").rsplit("/", 1)[-1].casefold()


def lua_string(value: str) -> str:
    # JSON escaping is not Lua escaping; decimal UTF-8 bytes are unambiguous.
    return '"' + "".join(f"\\{b:03d}" for b in value.encode("utf-8")) + '"'


def quest_probe_lua(quest: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", quest):
        raise ValueError("Source quest must be a bounded identifier")
    return """
local name=QUEST_NAME
local out={quest=name,readOnly=true,methods={},observations={},objectiveAdapter='missing',
  limitation='False may mean absent quest; registration and getters do not prove objective progression.'}
if type(QuestSystem)~='table' then out.status='unavailable';out.globalType=type(QuestSystem);return out end
for _,method in ipairs({'IsQuestStarted','IsQuestCompleted','IsQuestAvailable','IsQuestCanceled'}) do
  local f=QuestSystem[method];out.methods[method]=type(f)
  if type(f)=='function' then
    local ok,value=pcall(f,name)
    if ok and (type(value)=='boolean' or type(value)=='number' or type(value)=='string') then
      out.observations[method]={status='read',value=value}
    else out.observations[method]={status=ok and 'unsupported_return' or 'error',detail=tostring(value)} end
  end
end
out.status='observed'
return out
""".replace("QUEST_NAME", lua_string(quest))


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported acceptance manifest schema")
    if not isinstance(manifest.get("destination_level"), str) or not level_name(manifest["destination_level"]):
        raise ValueError("Manifest requires a destination_level")
    if manifest.get("opening", {}).get("dispatch_must_not_be_called_by_acceptance_tests") is not True:
        raise ValueError("Manifest must explicitly prohibit synthetic opening dispatch")
    cases = manifest.get("acceptance")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest requires acceptance cases")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(case, str) or not re.fullmatch(r"[a-z_]+", case) for case in ids) or len(ids) != len(set(ids)):
        raise ValueError("Acceptance case identities must be unique bounded names")
    entities = manifest.get("inventory", {}).get("entities")
    if not isinstance(entities, list):
        raise ValueError("Manifest requires source entities")
    source_ids = [e.get("source_id") for e in entities]
    if any(not isinstance(v, str) or not v for v in source_ids) or len(set(source_ids)) != len(source_ids):
        raise ValueError("Source entity identities must be unique")


def build_plan(manifest: dict[str, Any], quest_ir: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_manifest(manifest)
    entities = manifest["inventory"]["entities"]
    actors = [e for e in entities if e.get("source_class") in ("NPC", "NPC_Female")]
    # The bounds derive from authored opening NPC placements, never a teleport.
    points = [point(e.get("position")) for e in actors if e.get("name", "").casefold().startswith("ska_")]
    points = [p for p in points if p]
    bounds = {"minimum": {a: min(p[a] for p in points) for a in "xyz"},
              "maximum": {a: max(p[a] for p in points) for a in "xyz"}, "horizontalMargin": 50} if points else None
    objectives = []
    if quest_ir is not None:
        if quest_ir.get("quest") != manifest["quest"]:
            raise ValueError("Quest IR does not belong to the acceptance quest")
        for row in quest_ir.get("tables", {}).get("quest_objective", {}).get("rows", []):
            objectives.append({k: row.get(k) for k in ("objective_id", "objective_name", "is_hidden", "condition", "autocomplete_timeout_str", "expiration_timeout_str")})
    return {
        "schema": 1, "created_utc": utc(), "source_level": manifest["source_level"],
        "destination_level": manifest["destination_level"], "quest": manifest["quest"],
        "source_profile": manifest["inventory"].get("profile"),
        "source_entity_counts": dict(Counter(e.get("source_class", "unknown") for e in entities)),
        "source_opening_bounds": bounds,
        "source_objectives": [dict(o, status="not_tested", missing_adapter="native objective identity/state plus authored interaction route") for o in objectives],
        "controller_conversion_gap_count": len(manifest.get("controller_conversion_gaps", [])),
        "acceptance": [dict(case, status="not_tested", evidence=[], missing_adapters=MISSING_ADAPTERS.get(case["id"], ["source-authored interaction and observable outcome adapter"])) for case in manifest["acceptance"]],
        "prohibited_substitutes": ["teleport in place of traversal", "quest completion/start dispatch in place of gameplay", "registration as gameplay success", "editing or overwriting real saves"],
        "fully_verified": False,
    }


class RpcError(RuntimeError):
    pass


class McpProcess:
    """Small real JSON-RPC stdio client, with deadlines and captured stderr."""
    def __init__(self, executable: Path, evidence: Path):
        self.counter = 0
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.errors = (evidence / "mcp-stderr.log").open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.errors, text=True, encoding="utf-8", bufsize=1,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            self.errors.close()
            raise
        try:
            threading.Thread(target=self._read, daemon=True).start()
            self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "SkalitzAcceptance", "version": "0.1.0"}})
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            self.process.stdin.flush()
            listing = self.request("tools/list", {})
            self.tools = {tool["name"] for tool in listing.get("tools", [])}
        except Exception:
            self.close()
            raise

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def request(self, method: str, params: dict[str, Any], timeout: float = 20) -> dict[str, Any]:
        self.counter += 1
        identifier = self.counter
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise RpcError("MCP response deadline elapsed")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise RpcError("MCP response deadline elapsed") from exc
            if line is None:
                raise RpcError("MCP process disconnected")
            if len(line) > 8_000_000:
                raise RpcError("MCP response exceeded the evidence size limit")
            response = json.loads(line)
            if response.get("id") != identifier:
                continue
            if "error" in response:
                raise RpcError(str(response["error"]))
            return response.get("result", {})

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in self.tools:
            raise RpcError("Required MCP tool unavailable: " + tool)
        reply = self.request("tools/call", {"name": tool, "arguments": arguments})
        texts = [block["text"] for block in reply.get("content", []) if block.get("type") == "text"]
        if len(texts) != 1:
            raise RpcError("Expected one structured MCP text result")
        value = json.loads(texts[0])
        if reply.get("isError"):
            value["mcp_is_error"] = True
        return value

    def close(self) -> None:
        try:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Only the subprocess this runner created, never the game.
                self.process.terminate()
                self.process.wait(timeout=3)
        finally:
            self.errors.close()


def payload(response: dict[str, Any]) -> dict[str, Any]:
    current = response
    for _ in range(3):
        if current.get("ok") is False or current.get("mcp_is_error"):
            raise RpcError(str(current.get("error", "MCP operation failed")))
        if isinstance(current.get("result"), dict) and "ok" in current:
            current = current["result"]
        else:
            return current
    return current


class EvidenceRunner:
    def __init__(self, client: Any, manifest: dict[str, Any], plan: dict[str, Any], output: Path,
                 mode: str = "observe", session: str | None = None, entity_limit: int = 12):
        self.client, self.manifest, self.report, self.output = client, manifest, plan, output
        self.mode, self.session, self.entity_limit = mode, session, entity_limit
        self.sequence = 0
        self.active_task: str | None = None
        self.guard_pid: int | None = None
        self.report.update(mode=mode, observations=[], checks=[], finished_utc=None)

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool in GAMEPLAY_TOOLS and self.mode != "exercise":
            raise RpcError("Gameplay mutation is disabled in this mode")
        self.sequence += 1
        record = {"utc": utc(), "tool": tool, "arguments": arguments or {}}
        relative = f"evidence/{self.sequence:04d}-{tool}.json"
        try:
            response = self.client.call(tool, arguments or {})
            record["response"] = response
            result = payload(response)
        except Exception as exc:
            record["error"] = str(exc)
            raise
        finally:
            write_json(self.output / relative, record)
            self.report["observations"].append({"tool": tool, "evidence": relative})
            self.flush()
        return result

    def flush(self) -> None:
        write_json(self.output / "report.json", self.report)

    def check(self, identity: str, status: str, detail: str, **fields: Any) -> None:
        self.report["checks"].append(dict(id=identity, status=status, detail=detail, **fields))
        self.flush()

    def exact_world(self, state: dict[str, Any]) -> None:
        if level_name(state.get("map")) != level_name(self.manifest["destination_level"]):
            raise RpcError("Active world is not the source manifest's destination level")

    def verify_ephemeral(self) -> dict[str, Any]:
        if self.mode != "exercise" or not self.session or not re.fullmatch(r"[0-9a-f]{32}", self.session):
            raise RpcError("Exercise requires an explicitly supplied prepared ephemeral session")
        status = self.call("native_menu_status")
        if status.get("session") != self.session:
            raise RpcError("Runtime ephemeral session does not match the supplied receipt")
        cvars = status.get("cvars", {})
        if str(cvars.get("wh_sys_FreezePlayline")) not in ("1", "1.0") or str(cvars.get("wh_sys_NoPlaylineDeleting")) not in ("1", "1.0"):
            raise RpcError("Both native save protection switches must read back enabled")
        runtime = self.call("runtime_status")
        pid = runtime.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RpcError("Runtime process identity is unavailable")
        if self.guard_pid is not None and pid != self.guard_pid:
            raise RpcError("Game process changed during the acceptance run")
        self.guard_pid = pid
        saved = self.call("native_menu_verify_saves", {"session": self.session})
        if saved.get("ok") is not True or saved.get("changed") or saved.get("removed") or saved.get("added"):
            raise RpcError("Save manifest changed or verification did not positively succeed")
        state = self.call("player_state")
        self.exact_world(state)
        pos = point(state.get("entity", {}).get("position"))
        bounds = self.report.get("source_opening_bounds")
        if not pos or not bounds:
            raise RpcError("Source-derived Skalitz position bounds cannot be verified")
        margin = bounds["horizontalMargin"]
        if any(not bounds["minimum"][a] - margin <= pos[a] <= bounds["maximum"][a] + margin for a in ("x", "y")):
            raise RpcError("Henry is outside the source opening NPC placement bounds")
        return state

    def observe_entities(self, state: dict[str, Any]) -> None:
        pos = point(state.get("entity", {}).get("position"))
        candidates = [e for e in self.manifest["inventory"]["entities"] if e.get("source_class") in ("NPC", "NPC_Female", "AnimDoor", "Shop", "PickableItem")]
        def distance(e: dict[str, Any]) -> float:
            p = point(e.get("position"))
            return sum((p[a] - pos[a]) ** 2 for a in ("x", "y", "z")) if p and pos else math.inf
        candidates.sort(key=lambda e: (distance(e), e.get("source_id", "")))
        chosen = candidates[:self.entity_limit]
        self.report["selected_source_entities"] = [{k: e.get(k) for k in ("source_id", "name", "source_guid", "source_class", "position")} for e in chosen]
        for source in chosen:
            name = source.get("name")
            if not isinstance(name, str) or not name:
                continue
            try:
                observed = self.call("inspect_npc", {"name": name, "includeInventory": source.get("source_class") in ("NPC", "NPC_Female", "Shop"), "inventoryLimit": 30})
                self.check("source_entity:" + source["source_id"], "observed", "Runtime identity/property evidence only; behavior remains untested", source_name=name,
                           runtime_identity=observed.get("entity"), source_soul_guid=source.get("soul", {}).get("guid"))
            except RpcError as exc:
                self.check("source_entity:" + source["source_id"], "unavailable", str(exc), source_name=name)

    def movement(self) -> None:
        before = self.verify_ephemeral()
        existing = self.call("henry_task_status")
        if existing.get("state") == "running":
            raise RpcError("An unrelated Henry task is already running")
        task = self.call("henry_task_start", {
            "kind": "sequence", "maxSeconds": 5, "probeAction": "moveforward",
            "steps": [{"kind": "hold", "action": "moveforward", "seconds": 0.5, "value": 0.5}],
            "expect": {"playerDisplacement": 0.05},
        })
        self.active_task = task.get("id")
        if not isinstance(self.active_task, str):
            raise RpcError("Henry task returned no identity; outcome unknown")
        end = time.monotonic() + 8
        try:
            while task.get("state") == "running" and time.monotonic() < end:
                time.sleep(0.2)
                task = self.call("henry_task_status")
                if task.get("id") != self.active_task:
                    raise RpcError("Henry task identity changed during observation")
            if task.get("state") == "running":
                raise RpcError("Bounded movement task did not finish within its host deadline")
            self.active_task = None
        finally:
            if self.active_task:
                current = self.call("henry_task_status")
                if current.get("id") == self.active_task and current.get("state") == "running":
                    self.call("henry_task_cancel")
                self.active_task = None
        after = self.verify_ephemeral()
        before_pos, after_pos = point(before.get("entity", {}).get("position")), point(after.get("entity", {}).get("position"))
        displacement = math.dist(list(before_pos.values()), list(after_pos.values())) if before_pos and after_pos else None
        observed_pass = task.get("state") == "completed" and task.get("movementProbe", {}).get("passed") is True and task.get("evaluation", {}).get("verdict") == "passed" and displacement is not None and displacement >= 0.05
        self.check("bounded_player_movement", "passed" if observed_pass else "failed",
                   "Observed movement only; doorway collision, actor body rendering and campaign opening are separate tests",
                   before=before.get("entity"), after=after.get("entity"), displacement=displacement,
                   task_state=task.get("state"), reason=task.get("reason"))

    def run(self) -> dict[str, Any]:
        try:
            missing = sorted(REQUIRED_READ_TOOLS - self.client.tools)
            if missing:
                raise RpcError("Required inspection tools unavailable: " + ", ".join(missing))
            self.report["mcp_tools"] = sorted(self.client.tools)
            self.call("runtime_status")
            state = self.call("player_state", {"includeInventory": True, "inventoryLimit": 100})
            self.exact_world(state)
            self.check("destination_world", "passed", "Exact destination identifier observed; this does not establish playable simulation")
            self.call("list_entities", {"scope": "nearby", "radius": 100, "limit": 100})
            self.observe_entities(state)
            if "execute_lua" in self.client.tools:
                self.call("execute_lua", {"code": quest_probe_lua(self.manifest["quest"])})
            else:
                self.check("quest_state_observation", "unavailable", "Read-only native quest getter adapter unavailable")
            if self.mode == "exercise":
                self.movement()
        except Exception as exc:
            self.check("run_boundary", "blocked", str(exc))
        finally:
            if self.mode == "exercise" and self.session:
                try:
                    self.call("native_menu_verify_saves", {"session": self.session})
                except Exception as exc:
                    self.check("save_protection_final", "failed", str(exc))
            # Runtime observations are references on acceptance cases, not passes.
            for case in self.report["acceptance"]:
                case["status"] = "incomplete"
                case["evidence"] = [o["evidence"] for o in self.report["observations"]]
            self.report["fully_verified"] = False
            self.report["finished_utc"] = utc()
            self.flush()
        return self.report


def log_interval(path: Path, before: tuple[int, int] | None, output: Path) -> dict[str, Any]:
    if not path.is_file() or before is None:
        return {"status": "unavailable", "reason": "Game log was not available at the start and end"}
    info = path.stat()
    start = before[0] if info.st_ino == before[1] and info.st_size >= before[0] else 0
    limit = 5 * 1024 * 1024
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(limit)
    destination = output / "game-log-interval.txt"
    destination.write_bytes(chunk)
    content = chunk.decode("utf-8", errors="replace")
    matches = [line[:2000] for line in content.splitlines() if re.search(r"fatal|crash|\[Error\]|shader.*(fail|cap)|material.*(fail|limit)|HLOD.*timed out", line, re.I)]
    return {"status": "observed", "path": destination.name, "bytes": len(chunk), "truncated": info.st_size - start > limit,
            "rotated_or_truncated": start == 0 and before[0] != 0, "error_lines": matches[:200],
            "limitation": "No matching log line does not establish visual or gameplay success"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--quest-ir", type=Path, default=DEFAULT_IR)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--mcp", type=Path, default=DEFAULT_MCP)
    parser.add_argument("--mode", choices=("plan", "observe", "exercise"), default="plan")
    parser.add_argument("--session", help="Prepared KCD2Mcp ephemeral native-menu session; required for exercise")
    parser.add_argument("--entity-limit", type=int, default=12)
    parser.add_argument("--game-log", type=Path, default=DEFAULT_GAME_LOG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.entity_limit <= 64:
        parser.error("entity-limit must be between 1 and 64")
    if args.mode == "exercise" and not re.fullmatch(r"[0-9a-f]{32}", args.session or ""):
        parser.error("exercise requires the already prepared --session")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    ir = json.loads(args.quest_ir.read_text(encoding="utf-8")) if args.quest_ir.is_file() else None
    plan = build_plan(manifest, ir)
    plan["source_files"] = [{"path": str(path.resolve()), "sha256": digest(path)} for path in (args.manifest, args.spec, args.quest_ir) if path.is_file()]
    output = args.output or ROOT / "outputs/CurrentBuild/mcp-tests" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new evidence output directory")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "plan.json", plan)
    if args.mode == "plan":
        print(json.dumps({"mode": "plan", "acceptance_families": len(plan["acceptance"]), "source_objectives": len(plan["source_objectives"]), "output": str(output), "fully_verified": False}))
        return 0
    log_start = (args.game_log.stat().st_size, args.game_log.stat().st_ino) if args.game_log.is_file() else None
    client = McpProcess(args.mcp, output)
    try:
        runner = EvidenceRunner(client, manifest, plan, output, args.mode, args.session, args.entity_limit)
        report = runner.run()
        report["game_log"] = log_interval(args.game_log, log_start, output)
        runner.flush()
    finally:
        client.close()
    failed = any(c["status"] in ("blocked", "failed") for c in report["checks"])
    print(json.dumps({"mode": args.mode, "checks": dict(Counter(c["status"] for c in report["checks"])), "output": str(output), "fully_verified": False}))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
