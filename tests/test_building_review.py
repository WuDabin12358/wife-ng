import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from BuildingBlueprint import BlueprintStore, compile_blueprint
from BuildingReview import review_design
from BuildingContext import BuildingContext
import WifeAgent as agent_module
from test_site_layout import connected_design


class BuildingReviewTests(unittest.TestCase):
    def review(self,d):
        compiled,voxels=compile_blueprint(d)
        return review_design(d,compiled,voxels)

    def test_diagonal_column_is_reported_when_straight_window_rays_pass(self):
        d=connected_design()
        d['operations'].append({'kind':'fill','from':[1,2,-1],'to':[1,3,-1],
                                'material':'minecraft:oak_log[axis=y]'})
        report=self.review(d)
        self.assertTrue(any(f['code']=='WINDOW_SIDE_OBSTRUCTION' for f in report['findings']))
        self.assertTrue(any(s.get('first_block')==[21,6,19] for w in report['windows'] for s in w['samples']))

    def test_unspecified_diagonal_cells_remain_unknown(self):
        report=self.review(connected_design())
        self.assertGreater(sum(w['ray_counts']['unknown'] for w in report['windows']),0)

    def test_wide_window_review_counts_all_rays_but_labels_examples_as_partial(self):
        report=self.review(connected_design())
        window=report['windows'][0]
        self.assertEqual(window['total_rays'],18)
        self.assertEqual(sum(window['ray_counts'].values()),18)
        self.assertEqual(len(window['samples']),12)
        self.assertTrue(window['samples_are_examples'])

    def test_tree_on_grass_is_a_blocker_but_stable_base_is_not(self):
        d=connected_design()
        d['operations'] += [{'kind':'block','at':[20,0,0],'material':'minecraft:grass_block'},
                            {'kind':'block','at':[20,1,0],'material':'minecraft:oak_log[axis=y]'}]
        self.assertTrue(any(f['code']=='TREE_BASE_GRASS' for f in self.review(d)['findings']))
        d['operations'][-2]['material']='minecraft:coarse_dirt'
        self.assertFalse(any(f['code']=='TREE_BASE_GRASS' for f in self.review(d)['findings']))

    def test_review_is_read_only_and_does_not_change_saved_blueprint_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=BlueprintStore(tmp); bid=store.save(connected_design())['blueprint_id']
            before=(store.directory/(bid+'.json')).read_bytes()
            self.assertEqual(store.review(bid)['blueprint_id'],bid)
            self.assertEqual((store.directory/(bid+'.json')).read_bytes(),before)
            self.assertEqual(store.load(bid)['id'],bid)

    def test_supported_covered_leftover_is_reported_without_assuming_unknown_air(self):
        d=connected_design()
        d['operations'] += [{'kind':'fill','from':[14,0,1],'to':[16,0,4],'material':'minecraft:stone_bricks'},
                            {'kind':'fill','from':[14,4,1],'to':[16,4,4],'material':'minecraft:oak_planks'}]
        report=self.review(d)
        pocket=next(p for p in report['unprogrammed_pockets'] if p['from']==[34,5,21])
        self.assertEqual(pocket['floor_cells'],12)
        self.assertEqual(pocket['unspecified_headroom_cells'],12)

    def test_unstable_design_cannot_submit_any_world_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=connected_design()
            d['operations'] += [{'kind':'block','at':[20,0,0],'material':'minecraft:grass_block'},
                                {'kind':'block','at':[20,1,0],'material':'minecraft:oak_log[axis=y]'}]
            agent=agent_module.WifeAgent(); agent.blueprints=BlueprintStore(tmp)
            bid=agent.blueprints.save(d)['blueprint_id']
            with patch.object(agent_module,'http_json') as network:
                with self.assertRaisesRegex(ValueError,'blocking design findings'):
                    agent.submit_tool('build_blueprint',{'blueprint_id':bid,'dimension':'minecraft:overworld'})
            network.assert_not_called()

    def test_planning_loop_requires_review_before_actual_build_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=agent_module.WifeAgent(); agent.blueprints=BlueprintStore(tmp)
            agent.building_context=BuildingContext(tmp)
            bid=agent.blueprints.save(connected_design())['blueprint_id']
            calls=[]
            for i,name in enumerate(['build_blueprint','review_blueprint','build_blueprint','verify_blueprint']):
                args={'blueprint_id':bid}
                if name!='review_blueprint': args['dimension']='minecraft:overworld'
                calls.append({'tool_calls':[{'id':str(i),'function':{'name':name,'arguments':json.dumps(args)}}]})
            calls.append({'content':'施工与结构复核已完成。'})
            responses=iter(calls); transcripts=[]; writes=[]
            def model(messages,tools):
                transcripts.append(copy.deepcopy(messages))
                return next(responses)
            def http(url,method='GET',payload=None,*args,**kwargs):
                if url.endswith('/v1/state'): return {'connected':True,'self':{'creative':True}}
                if url.endswith('/v1/capabilities'): return {'tools':[{'name':n} for n in ('build_blueprint','verify_blueprint')]}
                if url.endswith('/v1/tasks'): return []
                if url.endswith('/v1/tools'):
                    writes.append(payload); return {'task':{'id':'task'}}
                return {'id':'task','state':'SUCCEEDED','result':{'verified':True,'blueprint_id':bid,'mismatch_count':0}}
            agent.call_model=model; agent.say_text=lambda *args:None
            with patch.object(agent_module,'http_json',side_effect=http):
                agent.handle({'player':'Wu_Dabin','message':'wife 建造现有别墅方案'})
            self.assertIn('review_blueprint',transcripts[1][-1]['content'])
            self.assertEqual([p['tool'] for p in writes],['build_blueprint','verify_blueprint'])
