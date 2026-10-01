import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from BuildingBlueprint import BlueprintStore, compile_blueprint
import json


def layout():
    return {"name":"functional room","style":"modern","design_intent":"window, study and circulation",
            "origin":[20,4,20],"palette":{"wall":"minecraft:white_concrete"},"checkpoints":[],"rooms":[],
            "operations":[
                {"kind":"room","from":[0,0,0],"to":[12,6,9],"entry":[6,1,0],"material":"wall","label":"hall"},
                {"kind":"fill","from":[2,2,0],"to":[4,3,0],"material":"minecraft:glass"},
                {"kind":"fill","from":[2,2,-3],"to":[4,3,-1],"material":"minecraft:air"},
                {"kind":"block","at":[2,1,5],"material":"minecraft:quartz_stairs[facing=west,half=bottom,shape=straight]"},
                {"kind":"block","at":[4,1,5],"material":"minecraft:oak_slab[type=top]"}],
            "interiors":[{"room":"hall","zones":[{"name":"study","purpose":"study","from":[1,1,1],"to":[11,1,8]}],
                "furniture":[
                    {"name":"chair","type":"chair","zone":"study","from":[2,1,5],"to":[2,1,5],"facing":"east","faces":"desk","access":[[2,1,6]]},
                    {"name":"desk","type":"desk","zone":"study","from":[4,1,5],"to":[4,1,5],"facing":"west","access":[[4,1,6]]}],
                "circulation":[{"name":"main route","from":[6,1,1],"to":[7,1,8]}]}],
            "windows":[{"name":"north window","room":"hall","from":[2,2,0],"to":[4,3,0],"facing":"north"}]}


def add_bed(d):
    d["operations"] += [
        {"kind":"block","at":[3,1,3],"material":"minecraft:red_bed[part=foot,facing=north]"},
        {"kind":"block","at":[3,1,2],"material":"minecraft:red_bed[part=head,facing=north]"},
        {"kind":"fill","from":[3,1,1],"to":[3,2,1],"material":"minecraft:dark_oak_planks"}]
    d["interiors"][0]["zones"].append({"name":"sleep","purpose":"bedroom","from":[3,1,1],"to":[4,1,3]})
    d["interiors"][0]["furniture"].append({"name":"bed","type":"bed","zone":"sleep","from":[3,1,2],"to":[3,1,3],"facing":"north","access":[[4,1,2],[4,1,3]]})


class InteriorTests(unittest.TestCase):
    def test_valid_layout_checks_scale_access_routes_and_window(self):
        compiled,_=compile_blueprint(layout())
        self.assertTrue(compiled["summary"]["interiors"]["rooms"][0]["functional_access_verified"])
        self.assertEqual(compiled["summary"]["interiors"]["windows"][0]["clear_view_cells"],6)

    def test_rotated_bed_and_furniture_keep_their_functional_relationships(self):
        d=layout(); add_bed(d); d["orientation"]="east"
        compile_blueprint(d)

    def test_giant_sofa_is_rejected_even_when_room_has_enough_empty_space(self):
        d=layout()
        d["operations"].append({"kind":"fill","from":[2,1,5],"to":[8,2,7],"material":"minecraft:light_gray_wool"})
        d["interiors"][0]["furniture"]=[{"name":"giant sofa","type":"sofa","zone":"study","from":[2,1,5],"to":[8,2,7],"facing":"north","access":[[2,1,4]]}]
        with self.assertRaisesRegex(ValueError,"oversized"): compile_blueprint(d)

    def test_seat_back_must_face_away_from_the_table(self):
        d=layout(); d["operations"][3]["material"]="minecraft:quartz_stairs[facing=east]"
        with self.assertRaisesRegex(ValueError,"raised back"): compile_blueprint(d)

    def test_solid_cube_table_is_rejected(self):
        d=layout(); d["operations"][4]["material"]="minecraft:oak_planks"
        with self.assertRaisesRegex(ValueError,"thin"): compile_blueprint(d)

    def test_work_table_cannot_be_at_the_same_height_as_its_seat(self):
        d=layout(); d["operations"][4]["material"]="minecraft:oak_slab[type=bottom]"
        with self.assertRaisesRegex(ValueError,"above chair"): compile_blueprint(d)

    def test_thin_pressure_plate_on_a_post_forms_a_valid_raised_table(self):
        d=layout(); d["operations"][4]["material"]="minecraft:oak_planks"
        d["operations"].append({"kind":"block","at":[4,2,5],"material":"minecraft:oak_pressure_plate[powered=false]"})
        d["interiors"][0]["furniture"][1]["to"]=[4,2,5]
        compile_blueprint(d)

    def test_rotating_the_room_preserves_table_seat_height_and_facing_checks(self):
        d=layout(); d["orientation"]="west"
        compile_blueprint(d)
        d["operations"][4]["material"]="minecraft:oak_slab[type=bottom]"
        with self.assertRaisesRegex(ValueError,"above chair"): compile_blueprint(d)

    def test_dining_chair_must_face_its_table_even_without_optional_faces(self):
        d=layout(); chair=d["interiors"][0]["furniture"][0]
        del chair["faces"]; chair["facing"]="west"
        d["operations"][3]["material"]="minecraft:quartz_stairs[facing=east,half=bottom]"
        with self.assertRaisesRegex(ValueError,"faces away"): compile_blueprint(d)

    def test_upside_down_stair_is_not_a_seat(self):
        d=layout(); d["operations"][3]["material"]="minecraft:quartz_stairs[facing=west,half=top]"
        with self.assertRaisesRegex(ValueError,"Upside-down"): compile_blueprint(d)

    def test_open_vertical_trapdoor_is_not_a_tabletop(self):
        d=layout(); d["operations"][4]["material"]="minecraft:oak_trapdoor[half=top,open=true]"
        with self.assertRaisesRegex(ValueError,"horizontal tabletop"): compile_blueprint(d)

    def test_one_thin_cell_cannot_hide_a_table_made_of_full_cubes(self):
        d=layout(); d["operations"].append({"kind":"block","at":[4,1,4],"material":"minecraft:oak_planks"})
        desk=d["interiors"][0]["furniture"][1]; desk["from"]=[4,1,4]; desk["facing"]="west"
        with self.assertRaisesRegex(ValueError,"horizontal tabletop"): compile_blueprint(d)

    def test_old_low_table_remains_readable_but_its_revision_must_pass_current_rules(self):
        d=layout(); d["operations"][4]["material"]="minecraft:oak_slab[type=bottom]"
        old_compiled,_=compile_blueprint(d,interior_validation_version=1)
        with tempfile.TemporaryDirectory() as tmp:
            store=BlueprintStore(tmp)
            old_id=old_compiled["id"]
            (Path(tmp)/(old_id+".json")).write_text(json.dumps({"blueprint":d,"compiled":old_compiled}),encoding="utf-8")
            self.assertEqual(store.load(old_id)["id"],old_id)
            with self.assertRaisesRegex(ValueError,"above chair"): store.load(old_id,require_current_interiors=True)
            page=store.inspect(old_id,max_bytes=100000)
            self.assertEqual(page["interior_validation"]["saved_version"],1)
            delta=copy.deepcopy(d); delta["operations"]=[]
            with self.assertRaisesRegex(ValueError,"above chair"): store.save(delta,old_id)
            delta["operations"]=[{"kind":"block","at":[4,1,5],"material":"minecraft:oak_slab[type=top]"}]
            new_id=store.save(delta,old_id)["blueprint_id"]
            self.assertNotEqual(new_id,old_id)
            self.assertEqual(store.inspect(new_id,max_bytes=100000)["interior_validation"]["saved_version"],2)

    def test_historical_version_cannot_bypass_source_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=BlueprintStore(tmp); saved=store.save(layout()); path=Path(tmp)/(saved["blueprint_id"]+".json")
            payload=json.loads(path.read_text(encoding="utf-8")); payload["blueprint"]["origin"][0]+=1
            payload["interior_validation_version"]=1
            path.write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"integrity"): store.load(saved["blueprint_id"])

    def test_declared_view_cannot_face_another_wall(self):
        d=layout(); d["operations"].append({"kind":"fill","from":[2,2,-1],"to":[4,3,-1],"material":"minecraft:stone"})
        with self.assertRaisesRegex(ValueError,"blocked or unspecified exterior view"): compile_blueprint(d)

    def test_window_must_be_on_the_outward_boundary(self):
        d=layout(); d["windows"][0]["facing"]="south"
        with self.assertRaisesRegex(ValueError,"exterior boundary"): compile_blueprint(d)

    def test_cabinet_cannot_cover_the_window_from_inside(self):
        d=layout(); d["operations"].append({"kind":"fill","from":[2,1,1],"to":[4,3,1],"material":"minecraft:bookshelf"})
        with self.assertRaisesRegex(ValueError,"blocked from inside"): compile_blueprint(d)

    def test_a_good_window_cannot_hide_an_undeclared_bad_glass_opening(self):
        d=layout(); d["operations"].append({"kind":"block","at":[8,2,9],"material":"minecraft:glass"})
        with self.assertRaisesRegex(ValueError,"undeclared glass openings"): compile_blueprint(d)

    def test_bed_cannot_be_listed_as_a_random_study_object(self):
        d=layout(); add_bed(d); d["interiors"][0]["furniture"][-1]["zone"]="study"
        with self.assertRaisesRegex(ValueError,"bedroom functional zone"): compile_blueprint(d)

    def test_bed_needs_a_headboard_and_actual_side_access(self):
        d=layout(); add_bed(d)
        compile_blueprint(d)
        d["operations"].pop()
        with self.assertRaisesRegex(ValueError,"headboard"): compile_blueprint(d)

    def test_duplicate_bed_access_does_not_count_twice(self):
        d=layout(); add_bed(d)
        d["interiors"][0]["furniture"][-1]["access"]=[[4,1,2],[4,1,2]]
        with self.assertRaisesRegex(ValueError,"usable access"): compile_blueprint(d)

    def test_bed_foot_clearance_cannot_replace_mattress_side_access(self):
        d=layout(); add_bed(d)
        d["operations"] += [
            {"kind":"block","at":[4,1,3],"material":"minecraft:red_bed[part=foot,facing=north]"},
            {"kind":"block","at":[4,1,2],"material":"minecraft:red_bed[part=head,facing=north]"},
            {"kind":"fill","from":[4,1,1],"to":[4,2,1],"material":"minecraft:dark_oak_planks"}]
        d["interiors"][0]["furniture"][-1]["to"]=[4,1,3]
        d["interiors"][0]["furniture"][-1]["access"]=[[3,1,4],[4,1,4]]
        with self.assertRaisesRegex(ValueError,"beside its mattress"): compile_blueprint(d)

    def test_nightstand_can_occupy_head_side_while_other_side_and_foot_are_clear(self):
        d=layout(); add_bed(d)
        d["operations"].append({"kind":"block","at":[4,1,2],"material":"minecraft:oak_planks"})
        d["interiors"][0]["furniture"][-1]["access"]=[[4,1,3],[3,1,4]]
        compile_blueprint(d)

    def test_claimed_main_route_must_be_actually_clear(self):
        d=layout(); d["operations"].append({"kind":"fill","from":[6,1,4],"to":[6,2,4],"material":"minecraft:stone"})
        with self.assertRaisesRegex(ValueError,"blocks declared main circulation"): compile_blueprint(d)

    def test_new_design_cannot_bypass_layout_checks_with_an_empty_list(self):
        d=layout(); d["interiors"]=[]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,"require functional"): BlueprintStore(tmp).save(d,require_interiors=True)


if __name__=="__main__": unittest.main()
