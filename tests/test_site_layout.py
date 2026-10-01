import copy
import tempfile
import unittest
from BuildingBlueprint import BlueprintStore, compile_blueprint
from test_interiors import layout


def connected_design():
    d=layout()
    d['operations'] += [
        {'kind':'fill','from':[5,0,-2],'to':[7,0,-1],'material':'minecraft:stone_bricks'},
        {'kind':'fill','from':[5,1,-2],'to':[7,2,-1],'material':'minecraft:air'},
        {'kind':'fill','from':[6,1,0],'to':[7,2,0],'material':'minecraft:air'}]
    d['site_layout']={'spaces':[{'name':'entry court','purpose':'entry',
        'design_intent':'Front threshold, with a two-wide route into the hall.',
        'from':[5,1,-2],'to':[7,1,-1]}],
        'routes':[{'name':'main entrance','from_space':'entry court','to_space':'hall',
                   'purpose':'entry','width':2,'path':[[6,1,-2],[6,1,1]]}]}
    return d


class SiteLayoutTests(unittest.TestCase):
    def test_real_two_wide_connection_and_rotation(self):
        for orientation in ('north','east','south','west'):
            d=connected_design(); d['orientation']=orientation
            result,_=compile_blueprint(d)
            self.assertTrue(result['summary']['site_layout']['functional_connections_verified'])

    def test_graph_labels_cannot_authorize_walking_through_a_solid_wall(self):
        d=connected_design()
        d['operations'].append({'kind':'block','at':[7,1,0],'material':'minecraft:white_concrete'})
        with self.assertRaisesRegex(ValueError,'crosses a solid wall'): compile_blueprint(d)

    def test_unplanned_wall_hole_is_rejected_even_if_original_entry_works(self):
        d=connected_design()
        d['operations'].append({'kind':'fill','from':[12,1,6],'to':[12,2,6],'material':'minecraft:air'})
        with self.assertRaisesRegex(ValueError,'unplanned wall opening'): compile_blueprint(d)

    def test_named_but_disconnected_leftover_space_is_rejected(self):
        d=connected_design()
        d['site_layout']['spaces'].append({'name':'leftover','purpose':'utility',
            'design_intent':'A vague spare space','from':[15,1,1],'to':[16,1,4]})
        with self.assertRaisesRegex(ValueError,'disconnected from the entry'): compile_blueprint(d)

    def test_declared_route_width_is_checked_in_actual_blocks(self):
        d=connected_design(); d['site_layout']['routes'][0]['width']=1
        with self.assertRaisesRegex(ValueError,'at least two'): compile_blueprint(d)

    def test_walking_surface_cannot_be_an_explicit_air_hole(self):
        d=connected_design()
        d['operations'].append({'kind':'block','at':[7,0,-1],'material':'minecraft:air'})
        with self.assertRaisesRegex(ValueError,'walking surface'): compile_blueprint(d)

    def test_fake_outdoor_space_cannot_cover_existing_rooms(self):
        d=connected_design(); d['site_layout']['spaces'][0]['to']=[7,1,2]
        with self.assertRaisesRegex(ValueError,'cannot cover an indoor room'): compile_blueprint(d)

    def test_touching_outside_a_room_corner_does_not_make_a_real_connection(self):
        d=connected_design()
        d['site_layout']['routes'][0]['path']=[[6,1,-2],[0,1,-2],[0,1,0]]
        with self.assertRaisesRegex(ValueError,'endpoints must touch'): compile_blueprint(d)

    def test_historical_design_remains_readable_but_new_production_design_needs_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=BlueprintStore(tmp); old=store.save(layout())['blueprint_id']
            self.assertEqual(store.load(old)['id'],old)
            with self.assertRaisesRegex(ValueError,'require site_layout'):
                store.save(layout(),require_interiors=True,require_site_layout=True)
            self.assertTrue(store.review(old)['blocking_count'])
