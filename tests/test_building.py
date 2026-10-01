import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from BuildingBlueprint import BlueprintStore, compile_blueprint, rotate_state, structural_state
from BuildingContext import BuildingContext, output_budget, OUTPUT_TOKENS, INPUT_BYTE_BUDGET, input_token_upper_bound
import WifeAgent as agent_module


def design(ops=None):
    return {"name": "test", "style": "modern", "design_intent": "layered facade",
            "origin": [20, 4, 20], "palette": {"wall": "minecraft:white_concrete", "glass": "minecraft:glass"},
            "operations": ops or [{"kind": "hollow", "from": [0, 0, 0], "to": [8, 5, 8], "material": "wall"}],
            "checkpoints": ["entrance", "roof", "lighting"]}


class GeometryTests(unittest.TestCase):
    def test_openings_override_wall_and_air_is_specified(self):
        d = design()
        d["operations"].append({"kind": "fill", "from": [3, 1, 0], "to": [4, 2, 0], "material": "minecraft:air"})
        _, voxels = compile_blueprint(d)
        self.assertEqual(voxels[23, 5, 20], "minecraft:air")
        self.assertEqual(voxels[21, 5, 21], "minecraft:air")
        self.assertEqual(voxels[20, 4, 20], "minecraft:white_concrete")

    def test_rotated_world_coordinates_and_stair_facing(self):
        d = design([{"kind": "block", "at": [2, 1, 3], "material": "minecraft:oak_stairs[facing=north,half=bottom]"}])
        d["orientation"] = "east"
        _, voxels = compile_blueprint(d)
        self.assertEqual(voxels[17, 5, 22], "minecraft:oak_stairs[facing=east,half=bottom]")

    def test_directional_properties_and_axis_rotate(self):
        self.assertEqual(rotate_state("minecraft:oak_log[axis=x]", 1), "minecraft:oak_log[axis=z]")
        self.assertEqual(rotate_state("minecraft:glass_pane[north=true,east=false]", 1), "minecraft:glass_pane[east=true,south=false]")

    def test_nested_repeat_accumulates_offsets(self):
        d = design([{"kind": "repeat", "count": 3, "step": [4, 0, 0], "operations": [
            {"kind": "repeat", "count": 2, "step": [0, 0, 5], "operations": [
                {"kind": "block", "at": [0, 0, 0], "material": "wall"}]}]}])
        _, v = compile_blueprint(d)
        self.assertEqual(set(v), {(20+x, 4, 20+z) for x in (0, 4, 8) for z in (0, 5)})

    def test_frame_preserves_face_interior(self):
        _, v = compile_blueprint(design([{"kind": "frame", "from": [0, 0, 0], "to": [4, 4, 4], "material": "wall"}]))
        self.assertIn((20, 6, 20), v)
        self.assertNotIn((22, 6, 20), v)
        self.assertNotIn((22, 6, 22), v)

    def test_gable_ridge_and_open_interior(self):
        _, v = compile_blueprint(design([{"kind": "gable", "from": [0, 6, 0], "to": [8, 10, 10], "ridge_axis": "z", "material": "wall"}]))
        self.assertIn((24, 14, 25), v)
        self.assertIn((20, 10, 25), v)
        self.assertNotIn((24, 11, 25), v)

    def test_bad_roof_height_rejected(self):
        with self.assertRaises(ValueError):
            compile_blueprint(design([{"kind": "gable", "from": [0, 6, 0], "to": [8, 11, 10], "material": "wall"}]))

    def test_line_includes_both_endpoints(self):
        _, v = compile_blueprint(design([{"kind": "line", "from": [0, 0, 0], "to": [5, 5, 0], "material": "wall"}]))
        self.assertEqual(len(v), 6)
        self.assertIn((25, 9, 20), v)

    def test_volume_bounds_and_huge_overlap_rejected(self):
        for op in ([{"kind": "fill", "from": [0, 0, 0], "to": [127, 79, 127], "material": "wall"}],
                   [{"kind": "repeat", "count": 128, "step": [0, 0, 0], "operations": [{"kind": "fill", "from": [0, 0, 0], "to": [30, 30, 30], "material": "wall"}]}]):
            with self.assertRaises(ValueError): compile_blueprint(design(op))

    def test_fractional_bool_and_version_invalid(self):
        for value in (True, 0.5):
            d = design(); d["origin"][0] = value
            with self.assertRaises(ValueError): compile_blueprint(d)
        d = design(); d["palette"]["wall"] = "minecraft:cherry_planks"
        with self.assertRaises(ValueError): compile_blueprint(d)

    def test_command_injection_rejected(self):
        d = design(); d["palette"]["wall"] = "minecraft:stone\nkill @a"
        with self.assertRaises(ValueError): compile_blueprint(d)

    def test_blueprint_integrity_and_no_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BlueprintStore(tmp)
            summary = store.save(design())
            self.assertEqual(store.load(summary["blueprint_id"])["id"], summary["blueprint_id"])
            with self.assertRaises(ValueError): store.load("../../server.properties")
            path = Path(tmp) / (summary["blueprint_id"] + ".json")
            saved = json.loads(path.read_text())
            saved["blueprint"]["origin"][0] += 1
            path.write_text(json.dumps(saved))
            with self.assertRaises(ValueError): store.load(summary["blueprint_id"])

    def test_revision_freezes_old_palette_and_checks_combined_final_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BlueprintStore(tmp)
            old = store.save(design())
            delta = design([{"kind": "block", "at": [3, 1, 0], "material": "wall"}])
            delta["palette"]["wall"] = "minecraft:glass"
            updated = store.save(delta, old["blueprint_id"])
            compiled = store.load(updated["blueprint_id"])
            self.assertEqual(compiled["summary"]["supersedes_id"], old["blueprint_id"])
            self.assertEqual(compiled["operations"][0]["state"], "minecraft:white_concrete")
            self.assertEqual(compiled["operations"][-1]["state"], "minecraft:glass")

    def test_revision_execution_changes_only_final_different_cells_and_keeps_air_deletions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=BlueprintStore(tmp); old=store.save(design())
            delta=design([{"kind":"block","at":[2,2,0],"material":"glass"},
                          {"kind":"block","at":[3,2,0],"material":"minecraft:air"}])
            new=store.save(delta,old["blueprint_id"])
            plan=store.execution_plan(new["blueprint_id"])
            self.assertEqual(plan["changed_cells"],2)
            self.assertEqual({tuple(op["from"]) for op in plan["operations"]},{(22,6,20),(23,6,20)})
            self.assertIn("minecraft:air",[op["state"] for op in plan["operations"]])
            self.assertEqual(store.verification_plan(new["blueprint_id"])["target_cells"],486)

    def test_interactive_door_and_bed_state_are_distinct_from_geometry(self):
        self.assertEqual(structural_state("minecraft:oak_door[facing=north,half=lower,open=true,powered=true]"),
                         structural_state("minecraft:oak_door[facing=north,half=lower,open=false,powered=false]"))
        self.assertEqual(structural_state("minecraft:red_bed[part=head,facing=east,occupied=true]"),
                         structural_state("minecraft:red_bed[part=head,facing=east,occupied=false]"))
        self.assertNotEqual(structural_state("minecraft:oak_trapdoor[half=bottom,open=true]"),
                            structural_state("minecraft:oak_trapdoor[half=bottom,open=false]"))
        self.assertNotEqual(structural_state("minecraft:oak_door[facing=east,half=lower]"),
                            structural_state("minecraft:oak_door[facing=north,half=lower]"))

    def test_door_interaction_only_revision_does_not_rebuild_the_house(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=BlueprintStore(tmp)
            d=design(); d["operations"] += [{"kind":"block","at":[4,y,0],"material":"minecraft:oak_door[half="+part+",facing=north,open=false]"}
                                            for y,part in [(1,"lower"),(2,"upper")]]
            old=store.save(d)
            delta=design([{"kind":"block","at":[4,y,0],"material":"minecraft:oak_door[half="+part+",facing=north,open=true]"}
                          for y,part in [(1,"lower"),(2,"upper")]])
            new=store.save(delta,old["blueprint_id"])
            self.assertEqual(store.execution_plan(new["blueprint_id"])["operations"],[])
            self.assertTrue(all('open=' not in op['state'] for op in store.verification_plan(new["blueprint_id"])["operations"]))

    def test_inspection_pages_recover_the_exact_source_without_world_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BlueprintStore(tmp)
            original = design([{"kind":"block", "at":[i%9,0,i//9], "material":"wall", "label":"component "+str(i)} for i in range(90)])
            summary = store.save(original)
            recovered, offset, pages = [], 0, 0
            while True:
                page = store.inspect(summary["blueprint_id"],offset,max_bytes=2000)
                self.assertEqual(page["blueprint"]["origin"],original["origin"])
                self.assertLessEqual(len(json.dumps(json.dumps(page,ensure_ascii=False),ensure_ascii=False).encode("utf-8")),2000)
                recovered += page["blueprint"]["operations"]
                pages += 1
                offset = page["next_operation_start"]
                if offset is None: break
                self.assertLess(pages,100)
            self.assertGreater(pages,1)
            self.assertEqual(recovered,original["operations"])
            with self.assertRaises(ValueError): store.inspect("../server",max_bytes=2000)
            with self.assertRaises(ValueError): store.inspect(summary["blueprint_id"],-1,max_bytes=2000)

    def test_unknown_blocks_and_illegal_properties_fail_before_execution(self):
        for state in ("minecraft:unobtainium", "minecraft:oak_stairs[facing=up]", "minecraft:stone[waterlogged=true]"):
            d = design(); d["palette"]["wall"] = state
            with self.assertRaises(ValueError): compile_blueprint(d)

    def test_claimed_room_cannot_be_solid_or_have_blocked_entrance(self):
        d = design([{"kind": "fill", "from": [0, 0, 0], "to": [8, 5, 8], "material": "wall"}])
        d["rooms"] = [{"name": "hall", "from": [1, 1, 1], "to": [7, 4, 7], "entry": [4, 1, 0]}]
        with self.assertRaisesRegex(ValueError, "solid/unspecified"): compile_blueprint(d)
        d["operations"][0]["kind"] = "hollow"
        with self.assertRaisesRegex(ValueError, "entry must"): compile_blueprint(d)
        d["operations"].append({"kind": "fill", "from": [4, 1, 0], "to": [4, 2, 0], "material": "minecraft:air"})
        compiled, _ = compile_blueprint(d)
        self.assertEqual(compiled["summary"]["rooms"][0]["clear_footprint_cells"], 49)

    def test_incomplete_door_and_bed_fail_before_world_write(self):
        for state in ("minecraft:oak_door[half=lower,facing=north]", "minecraft:red_bed[part=foot,facing=north]"):
            with self.assertRaises(ValueError):
                compile_blueprint(design([{"kind": "block", "at": [0, 1, 0], "material": state}]))

    def test_semantic_rooms_generate_exact_shell_air_and_connected_entrance(self):
        compiled, v = compile_blueprint(design([{"kind": "room", "from": [0,0,0], "to": [8,5,8],
                                                "material": "wall", "entry": [4,1,0], "label": "hall"}]))
        self.assertEqual(v[24,5,20], "minecraft:air")
        self.assertEqual(v[24,6,20], "minecraft:air")
        self.assertEqual(compiled["summary"]["rooms"][0]["reachable_footprint_cells"], 49)


class ContextTests(unittest.TestCase):
    def test_restart_retains_design_and_mismatch_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            c.design({"blueprint_id": "a", "style": "modern"})
            c.task("verify_blueprint", {"state": "SUCCEEDED", "result": {"verified": False, "mismatches": [{"at": [2, 5, 6]}]}})
            restored = BuildingContext(tmp).checkpoint()
            self.assertEqual(restored["designs"]["a"]["style"], "modern")
            self.assertEqual(restored["last_tasks"][-1]["result"]["mismatches"][0]["at"], [2, 5, 6])

    def test_world_identity_prevents_stale_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WIFE_NG_WORLD_ID": "old"}):
                BuildingContext(tmp).design({"blueprint_id": "a"})
            with patch.dict(os.environ, {"WIFE_NG_WORLD_ID": "new"}):
                self.assertEqual(BuildingContext(tmp).checkpoint()["designs"], {})

    def test_compaction_keeps_original_goal_and_complete_tool_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": "x" * 9000},
                        {"role": "assistant", "tool_calls": [{"id": "t1"}]},
                        {"role": "tool", "tool_call_id": "t1", "content": "failure"}]
            result = c.compact_messages(messages, "villa with garden", {"self": {"creative": True}}, budget=2000)
            self.assertIn("villa with garden", result[1]["content"])
            self.assertEqual(result[-2:], messages[-2:])
            self.assertEqual(result[0], messages[0])

    def test_large_survey_is_cached_exactly_and_summary_marks_missing_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            scan = {"encoding": "world-x-runs-v1", "rows": ["x" * 600000], "palette": ["minecraft:air"], "complete": True}
            c.task("survey_region", {"state": "SUCCEEDED", "result": scan})
            self.assertNotIn("rows", c.checkpoint()["surveys"][0])
            self.assertIn("does not specify", c.checkpoint()["surveys"][0]["detail"])
            self.assertEqual(BuildingContext(tmp).data["surveys"][0]["rows"], scan["rows"])

    def test_output_budget_uses_context_and_api_limit_not_small_cost_cap(self):
        self.assertEqual(output_budget([{"role":"user","content":"design a villa"}], []), OUTPUT_TOKENS)
        self.assertLess(output_budget([{"role":"user","content":"x"*700000}], []), OUTPUT_TOKENS)

    def test_long_reasoning_tool_exchange_can_use_remaining_context_without_reserving_full_output_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages=[{'role':'system','content':'fixed'}, {'role':'user','content':'repair the whole layout'},
                      {'role':'assistant','reasoning_content':'x'*650000,'tool_calls':[{'id':'a'}]},
                      {'role':'tool','tool_call_id':'a','content':'width validation failed'}]
            compacted=BuildingContext(tmp).compact_messages(messages,'repair the whole layout',{})
            self.assertEqual(compacted,messages)
            self.assertGreater(output_budget(compacted,[]),250000)
            self.assertLess(output_budget(compacted,[]),OUTPUT_TOKENS)

    def test_truly_oversized_latest_exchange_is_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages=[{'role':'system','content':'fixed'}, {'role':'user','content':'repair'},
                      {'role':'assistant','reasoning_content':'x'*3000,'tool_calls':[{'id':'a'}]},
                      {'role':'tool','tool_call_id':'a','content':'failure'}]
            with self.assertRaisesRegex(ValueError,'Latest tool exchange exceeds context capacity'):
                BuildingContext(tmp).compact_messages(messages,'repair',{},budget=2000)

    def test_multiple_small_surveys_share_capacity_and_keep_disk_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            for i in range(3):
                c.task("survey_region", {"state":"SUCCEEDED", "result":{
                    "encoding":"world-x-runs-v2", "from":[i,0,0], "to":[i,1,1],
                    "dimension":"minecraft:overworld", "rows":["x"*200000], "palette":["minecraft:air"]}})
            view = c.checkpoint()
            self.assertLessEqual(len(json.dumps(view, ensure_ascii=False).encode("utf-8")), INPUT_BYTE_BUDGET // 2)
            self.assertTrue(all("rows" not in s and "does not specify" in s["detail"] for s in view["surveys"]))
            restored = BuildingContext(tmp)
            self.assertEqual(len(restored.data["surveys"]),3)
            self.assertEqual(len(restored.data["surveys"][0]["rows"][0]),200000)

    def test_checkpoint_archives_old_results_only_when_capacity_requires(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            for i in range(10):
                c.task("verify_blueprint", {"id":str(i), "state":"FAILED", "result":{"mismatches":["x"*300]}})
            view = c.checkpoint(max_bytes=2000)
            self.assertLessEqual(len(json.dumps(view, ensure_ascii=False).encode("utf-8")),2000)
            self.assertEqual(view["last_tasks"][-1]["id"],"9")
            self.assertGreater(view["archive"]["omitted"]["last_tasks"],0)
            self.assertEqual(len(BuildingContext(tmp).data["last_tasks"]),10)

    def test_compaction_keeps_intervening_diagnostics_and_all_tool_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            exchange = [{"role":"assistant","tool_calls":[{"id":"a"},{"id":"b"}]},
                        {"role":"system","content":"a specific entrance repair is required"},
                        {"role":"tool","tool_call_id":"a","content":"failed"},
                        {"role":"tool","tool_call_id":"b","content":"checked"}]
            messages = [{"role":"system","content":"fixed"},{"role":"user","content":"x"*9000}] + exchange
            tools = [{"type":"function","function":{"name":"repair","description":"x"*100}}]
            compacted = c.compact_messages(messages,"修好入口",{},budget=2000,tools=tools)
            self.assertEqual(compacted[-4:],exchange)
            self.assertLessEqual(input_token_upper_bound(compacted,tools),2000)

    def test_initial_context_compacts_before_first_model_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            messages = [{"role":"system","content":"fixed"},{"role":"user","content":"x"*9000}]
            result = c.compact_messages(messages,"中式庭院，灰瓦白墙",{"dimension":"minecraft:overworld"},budget=2000)
            self.assertIn("中式庭院",result[1]["content"])
            self.assertLessEqual(input_token_upper_bound(result),2000)

    def test_recall_finds_old_designs_after_checkpoint_eviction(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            c.design({"blueprint_id":"old-id", "name":"旧中式庭院", "style":"白墙灰瓦"})
            for i in range(8):
                c.design({"blueprint_id":str(i), "name":"new","style":"x"*400})
            self.assertNotIn("old-id",c.checkpoint(max_bytes=1500)["designs"])
            recalled = BuildingContext(tmp).recall("中式庭院",max_bytes=1500)
            self.assertEqual(recalled["matched_counts"]["designs"],1)
            self.assertEqual(recalled["designs"]["old-id"]["style"],"白墙灰瓦")

    def test_archive_reads_never_recursively_persist_their_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = BuildingContext(tmp)
            c.task("verify_blueprint",{"id":"one","state":"SUCCEEDED"})
            for _ in range(5):
                c.task("recall_building",{"state":"SUCCEEDED","result":c.recall("")})
                c.task("inspect_blueprint",{"state":"SUCCEEDED","result":{"blueprint":{"operations":[]}}})
            self.assertEqual(len(BuildingContext(tmp).data["last_tasks"]),1)


class AgentControlTests(unittest.TestCase):
    def test_viewing_an_existing_building_exposes_no_construction_tools(self):
        agent = agent_module.WifeAgent()
        exposed = []
        agent.call_model = lambda messages,tools: exposed.extend(t["function"]["name"] for t in tools) or {"content":"归档查看完成"}
        agent.say_text = lambda *args: None
        def http(url,*args,**kwargs):
            if url.endswith('/v1/state'): return {"connected":True,"self":{"creative":True}}
            if url.endswith('/v1/capabilities'): return {"tools":[{"name":name} for name in ("build_blueprint","verify_blueprint","say","survey_region","server_command")]}
            return []
        with patch.object(agent_module,"http_json",side_effect=http):
            agent.handle({"player":"Wu_Dabin","message":"wife 查看旧建筑蓝图"})
        self.assertIn("inspect_blueprint",exposed)
        self.assertIn("recall_building",exposed)
        self.assertNotIn("build_blueprint",exposed)
        self.assertNotIn("design_blueprint",exposed)
        self.assertNotIn("server_command",exposed)

    def test_stream_preserves_split_tool_arguments_reasoning_and_usage(self):
        chunks = [
            {"choices":[{"delta":{"reasoning_content":"synthetic planning"}}]},
            {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1","function":{"name":"design_blueprint","arguments":"{\"blue"}}]}}]},
            {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"print\":{}}"}}]},"finish_reason":"tool_calls"}], "usage":{"total_tokens":99}},
        ]
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def __iter__(self):
                return iter([('data: '+json.dumps(c)+'\n').encode() for c in chunks]+[b'data: [DONE]\n'])
        with patch.object(agent_module.urllib.request, "urlopen", return_value=Response()):
            result=agent_module.model_completion({"messages":[]}, "test-only")
        message=result["choices"][0]["message"]
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"blueprint":{}})
        self.assertEqual(message["reasoning_content"], "synthetic planning")
        self.assertEqual(result["usage"]["total_tokens"],99)

    def test_empty_permissions_expose_no_tools(self):
        self.assertEqual(agent_module.make_tools(set()), [])

    def test_cancel_during_model_call_cannot_start_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = agent_module.WifeAgent()
            agent.building_context = BuildingContext(tmp)
            def model(messages, tools):
                agent.cancel_generation += 1
                return {"tool_calls": [{"id": "x", "function": {"name": "build_blueprint", "arguments": "{}"}}]}
            agent.call_model = model
            submitted = []
            agent.submit_tool = lambda *args: submitted.append(args)
            def http(url, *args, **kwargs):
                if url.endswith('/v1/state'): return {"connected": True, "self": {"creative": True}}
                if url.endswith('/v1/capabilities'): return {"tools": [{"name": "build_blueprint"}]}
                return []
            with patch.object(agent_module, "http_json", side_effect=http):
                agent.handle({"player": "Wu_Dabin", "message": "wife 建造别墅"})
            self.assertEqual(submitted, [])

    def test_blueprint_execution_sends_compiled_ops_not_arbitrary_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = agent_module.WifeAgent()
            agent.blueprints = BlueprintStore(tmp)
            summary = agent.blueprints.save(design())
            sent = []
            def http(url, method="GET", payload=None, *args, **kwargs):
                if url.endswith('/v1/tools'):
                    sent.append(payload); return {"task": {"id": "t", "state": "RUNNING"}}
                return {"id": "t", "state": "SUCCEEDED"}
            with patch.object(agent_module, "http_json", side_effect=http):
                result = agent.submit_tool("build_blueprint", {"blueprint_id": summary["blueprint_id"], "dimension": "minecraft:overworld"})
            self.assertEqual(result["state"], "SUCCEEDED")
            self.assertEqual(sent[0]["tool"], "build_blueprint")
            self.assertGreater(len(sent[0]["arguments"]["operations"]), 0)

    def test_owner_stop_preserves_build_steps_as_paused_instead_of_cancelling_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=agent_module.WifeAgent(); agent.blueprints=BlueprintStore(tmp)
            summary=agent.blueprints.save(design()); requests=[]
            def http(url,method="GET",payload=None,*args,**kwargs):
                requests.append(url)
                if url.endswith('/v1/tools'):
                    agent.cancel_generation+=1
                    return {"task":{"id":"build","state":"RUNNING"}}
                return {"task":{"id":"build","state":"PAUSED"}}
            with patch.object(agent_module,'http_json',side_effect=http):
                result=agent.submit_tool('build_blueprint',{'blueprint_id':summary['blueprint_id'],'dimension':'minecraft:overworld'})
            self.assertEqual(result['state'],'PAUSED')
            self.assertTrue(any(url.endswith('/pause') for url in requests))
            self.assertFalse(any(url.endswith('/cancel') for url in requests))

    def test_external_pause_returns_promptly_without_auto_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=agent_module.WifeAgent(); agent.blueprints=BlueprintStore(tmp)
            summary=agent.blueprints.save(design()); requests=[]
            def http(url,method="GET",payload=None,*args,**kwargs):
                requests.append(url)
                if url.endswith('/v1/tools'): return {"task":{"id":"build","state":"RUNNING"}}
                return {"id":"build","state":"PAUSED"}
            with patch.object(agent_module,'http_json',side_effect=http):
                result=agent.submit_tool('build_blueprint',{'blueprint_id':summary['blueprint_id'],'dimension':'minecraft:overworld'})
            self.assertEqual(result['state'],'PAUSED')
            self.assertEqual(len(requests),2)
            self.assertFalse(any(url.endswith('/resume') for url in requests))

    def test_stop_during_blueprint_preparation_cannot_submit_a_late_world_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=agent_module.WifeAgent(); agent.blueprints=BlueprintStore(tmp)
            summary=agent.blueprints.save(design()); actual_plan=agent.blueprints.execution_plan
            def prepare(blueprint_id):
                plan=actual_plan(blueprint_id)
                agent.cancel_generation+=1
                return plan
            with patch.object(agent.blueprints,'execution_plan',side_effect=prepare),patch.object(agent_module,'http_json') as network:
                result=agent.submit_tool('build_blueprint',{'blueprint_id':summary['blueprint_id'],'dimension':'minecraft:overworld'},expected_generation=0)
            self.assertEqual(result['failure_code'],'OWNER_CANCELLED')
            network.assert_not_called()

    def test_already_cancelled_request_generation_is_rejected_before_any_tool_work(self):
        agent=agent_module.WifeAgent(); agent.cancel_generation=2
        with patch.object(agent_module,'http_json') as network:
            result=agent.submit_tool('resume_build',{'task_id':'old'},expected_generation=1)
        self.assertEqual(result['failure_code'],'OWNER_CANCELLED')
        network.assert_not_called()


if __name__ == "__main__": unittest.main()
