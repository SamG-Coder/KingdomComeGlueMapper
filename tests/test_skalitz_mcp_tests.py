"""Offline acceptance-runner safety/evidence tests. These never contact KCD2."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
SPEC = importlib.util.spec_from_file_location("skalitz_mcp_tests", Path(__file__).resolve().parents[1] / "tools/skalitz_mcp_tests.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
SESSION = "a" * 32


def manifest():
    return {"schema": 1, "source_level": "rataje", "destination_level": "kcd1_rataje", "quest": "q_skalitz",
            "opening": {"dispatch_must_not_be_called_by_acceptance_tests": True,
                        "dispatch": {"script": "DO_NOT_EXECUTE_SOURCE_SCRIPT()"}},
            "inventory": {"profile": "q_skalitz_normal", "entities": [
                {"source_id": "mother-layer#0", "name": "ska_motherOfHenry", "source_guid": "111", "source_class": "NPC_Female", "position": {"x": 0, "y": 0, "z": 50}, "soul": {"guid": "soul-mother"}},
                {"source_id": "door-layer#0", "name": "HomeDoor", "source_guid": "222", "source_class": "AnimDoor", "position": {"x": 1, "y": 0, "z": 50}},
            ]},
            "acceptance": [dict(id=identity, status="passed", action="Source gameplay action", evidence_required=["before/after"])
                           for identity in ("opening", "doors", "inventory", "terrain_collision", "quest_triggers")],
            "controller_conversion_gaps": [{"tree": "onUpdate"}]}


class FakeMcp:
    def __init__(self, world="kcd1_rataje", protected=True, session=SESSION, saves_ok=True):
        self.tools = m.REQUIRED_READ_TOOLS | {"native_menu_status", "native_menu_verify_saves", "execute_lua", "henry_task_status", "henry_task_start", "henry_task_cancel"}
        self.calls = []
        self.world, self.protected, self.session, self.saves_ok = world, protected, session, saves_ok
        self.pos = {"x": 0, "y": 0, "z": 50}
        self.task = {"ok": True, "state": "idle"}
        self.pid = 321

    def call(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == "runtime_status":
            return {"ok": True, "pid": self.pid}
        if tool == "player_state":
            return {"ok": True, "result": {"ok": True, "map": self.world, "present": True, "entity": {"name": "Henry", "position": dict(self.pos)}, "health": {"status": "read", "value": 100}}}
        if tool == "native_menu_status":
            return {"ok": True, "result": {"session": self.session, "cvars": {"wh_sys_FreezePlayline": 1 if self.protected else 0, "wh_sys_NoPlaylineDeleting": 1}}}
        if tool == "native_menu_verify_saves":
            return {"ok": self.saves_ok, "filesChecked": 7, "changed": [] if self.saves_ok else ["save.whs"], "removed": [], "added": []}
        if tool == "list_entities":
            return {"ok": True, "entities": [{"name": "ska_motherOfHenry"}]}
        if tool == "inspect_npc":
            return {"ok": True, "entity": {"name": args["name"]}, "properties": {"value": {"registered": True}}}
        if tool == "execute_lua":
            return {"ok": True, "result": {"status": "observed", "observations": {"IsQuestStarted": {"status": "read", "value": True}}, "objectiveAdapter": "missing"}}
        if tool == "henry_task_status":
            return self.task
        if tool == "henry_task_start":
            self.pos["y"] += 0.6
            self.task = {"ok": True, "id": "owned-task", "state": "completed", "movementProbe": {"passed": True}, "evaluation": {"verdict": "passed"}}
            return self.task
        if tool == "henry_task_cancel":
            self.task = {"ok": True, "state": "cancelled"}
            return self.task
        raise AssertionError("Unexpected tool: " + tool)


class PlanTests(unittest.TestCase):
    def test_source_plan_never_inherits_passes(self):
        plan = m.build_plan(manifest())
        self.assertFalse(plan["fully_verified"])
        self.assertTrue(all(c["status"] == "not_tested" for c in plan["acceptance"]))
        self.assertEqual(plan["source_opening_bounds"]["minimum"]["x"], 0)

    def test_objective_coverage_comes_from_source_ir(self):
        ir = {"quest": "q_skalitz", "tables": {"quest_objective": {"rows": [{"objective_id": "5", "objective_name": "sourceObjective"}]}}}
        result = m.build_plan(manifest(), ir)
        self.assertEqual(result["source_objectives"][0]["objective_name"], "sourceObjective")
        ir["quest"] = "unrelated"
        with self.assertRaises(ValueError):
            m.build_plan(manifest(), ir)

    def test_manifest_must_forbid_synthetic_dispatch(self):
        source = manifest()
        source["opening"]["dispatch_must_not_be_called_by_acceptance_tests"] = False
        with self.assertRaises(ValueError):
            m.build_plan(source)

    def test_duplicate_source_identity_is_rejected(self):
        source = manifest()
        source["inventory"]["entities"][1]["source_id"] = "mother-layer#0"
        with self.assertRaises(ValueError):
            m.build_plan(source)

    def test_unknown_acceptance_family_stays_missing(self):
        source = manifest()
        source["acceptance"].append({"id": "new_family", "status": "passed"})
        result = m.build_plan(source)
        self.assertEqual(result["acceptance"][-1]["status"], "not_tested")
        self.assertTrue(result["acceptance"][-1]["missing_adapters"])

    def test_quest_probe_contains_only_read_contract(self):
        code = m.quest_probe_lua("q_skalitz")
        for prohibited in ("StartQuest", "CompleteQuest", "SetQuest", "SendMessage", "ExecuteCommand", "SpawnEntity", "SetWorldPos"):
            self.assertNotIn(prohibited, code)
        with self.assertRaises(ValueError):
            m.quest_probe_lua("q_skalitz'); evil()")

    def test_payload_preserves_nested_errors(self):
        with self.assertRaises(m.RpcError):
            m.payload({"ok": True, "result": {"ok": False, "error": "inner error"}})
        self.assertEqual(m.payload({"ok": True, "result": {"ready": False}}), {"ready": False})


class RuntimeContractTests(unittest.TestCase):
    def run_case(self, client, mode="observe", session=None):
        source = manifest()
        with tempfile.TemporaryDirectory() as folder:
            runner = m.EvidenceRunner(client, source, m.build_plan(source), Path(folder), mode, session, entity_limit=2)
            result = runner.run()
            self.assertTrue((Path(folder) / "report.json").is_file())
            for record in result["observations"]:
                json.loads((Path(folder) / record["evidence"]).read_text())
            return result

    def assert_no_mutation(self, client):
        self.assertFalse(any(name in m.GAMEPLAY_TOOLS for name, _ in client.calls))

    def test_observe_does_not_act_or_pass_gameplay_from_presence(self):
        client = FakeMcp()
        report = self.run_case(client)
        self.assert_no_mutation(client)
        self.assertFalse(report["fully_verified"])
        self.assertTrue(all(c["status"] == "incomplete" for c in report["acceptance"]))
        self.assertTrue(any(c["status"] == "observed" for c in report["checks"]))
        self.assertFalse(any("DO_NOT_EXECUTE_SOURCE_SCRIPT" in str(args) for _, args in client.calls))

    def test_wrong_world_stops_before_entity_testing(self):
        client = FakeMcp(world="trosecko")
        report = self.run_case(client, "exercise", SESSION)
        self.assert_no_mutation(client)
        self.assertFalse(any(name == "inspect_npc" for name, _ in client.calls))
        self.assertTrue(any(c["status"] == "blocked" for c in report["checks"]))

    def test_public_client_missing_inspection_tools_reports_blocked(self):
        client = FakeMcp()
        client.tools = {"launch_game", "attach_plugin", "load_trosky", "runtime_status",
                        "inspect_world", "create_demo", "inspect_entity", "test_mechanics",
                        "remove_demo", "read_logs", "execute_lua"}
        report = self.run_case(client)
        self.assertEqual(client.calls, [])
        boundary = next(c for c in report["checks"] if c["id"] == "run_boundary")
        self.assertEqual(boundary["status"], "blocked")
        for name in ("player_state", "inspect_npc", "list_entities"):
            self.assertIn(name, boundary["detail"])
        self.assertFalse(report["fully_verified"])

    def test_no_session_refuses_gameplay(self):
        client = FakeMcp()
        self.run_case(client, "exercise")
        self.assert_no_mutation(client)

    def test_runtime_session_mismatch_refuses_gameplay(self):
        client = FakeMcp(session="b" * 32)
        self.run_case(client, "exercise", SESSION)
        self.assert_no_mutation(client)

    def test_save_protection_switches_required(self):
        client = FakeMcp(protected=False)
        self.run_case(client, "exercise", SESSION)
        self.assert_no_mutation(client)

    def test_changed_saves_refuse_gameplay(self):
        client = FakeMcp(saves_ok=False)
        report = self.run_case(client, "exercise", SESSION)
        self.assert_no_mutation(client)
        self.assertTrue(any(c["id"] == "save_protection_final" and c["status"] == "failed" for c in report["checks"]))

    def test_outside_authored_skalitz_area_refuses_gameplay(self):
        client = FakeMcp()
        client.pos["x"] = 10000
        self.run_case(client, "exercise", SESSION)
        self.assert_no_mutation(client)

    def test_unrelated_active_task_is_never_cancelled(self):
        client = FakeMcp()
        client.task = {"ok": True, "id": "someone-else", "state": "running"}
        self.run_case(client, "exercise", SESSION)
        self.assert_no_mutation(client)

    def test_verified_ephemeral_movement_is_narrow_pass_only(self):
        client = FakeMcp()
        report = self.run_case(client, "exercise", SESSION)
        self.assertEqual(sum(name == "henry_task_start" for name, _ in client.calls), 1)
        self.assertGreaterEqual(sum(name == "native_menu_verify_saves" for name, _ in client.calls), 3)
        self.assertTrue(any(c["id"] == "bounded_player_movement" and c["status"] == "passed" for c in report["checks"]))
        self.assertTrue(all(c["status"] == "incomplete" for c in report["acceptance"]))
        self.assertFalse(report["fully_verified"])

    def test_fake_task_pass_without_observed_displacement_fails(self):
        client = FakeMcp()
        original = client.call
        def call(tool, args):
            response = original(tool, args)
            if tool == "henry_task_start":
                client.pos["y"] = 0
            return response
        client.call = call
        report = self.run_case(client, "exercise", SESSION)
        self.assertTrue(any(c["id"] == "bounded_player_movement" and c["status"] == "failed" for c in report["checks"]))


if __name__ == "__main__":
    unittest.main()
