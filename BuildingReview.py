"""Geometric design review, separate from immutable blueprint integrity.

Reports measurements and review prompts; it cannot assign an aesthetic grade.
Missing specification remains unknown rather than being assumed transparent.
"""

DIRECTIONS = {'north': (0, -1), 'east': (1, 0), 'south': (0, 1), 'west': (-1, 0)}
REVIEW_VERSION = 1


def transparent(state):
    block = (state or '').split('[', 1)[0]
    return block == 'minecraft:air' or any(part in block for part in ('glass', '_leaves', '_fence', 'chain'))


def review_design(blueprint, compiled, voxels):
    findings = []
    windows = []
    floors = []
    if compiled['summary'].get('rooms') and 'site_layout' not in blueprint:
        findings.append({'code':'MISSING_SPACE_PROGRAM','severity':'blocking','subject':'whole building',
                         'message':'Rooms alone do not define the purpose of interstitial spaces or their connections. Add site_layout and validate functional routes before new construction.',
                         'at':[],'affected_cells':0})
    turns = list(DIRECTIONS).index(blueprint.get('orientation', 'north'))
    origin = blueprint['origin']

    def world(point):
        x, y, z = point
        for _ in range(turns): x, z = -z, x
        return x + origin[0], y + origin[1], z + origin[2]

    def finding(code, severity, subject, message, points):
        findings.append({'code': code, 'severity': severity, 'subject': subject,
                         'message': message, 'at': [list(p) for p in points[:12]],
                         'affected_cells': len(points)})

    for window in blueprint.get('windows', []):
        pa, pb = world(window['from']), world(window['to'])
        lo = tuple(min(pa[i], pb[i]) for i in range(3))
        hi = tuple(max(pa[i], pb[i]) for i in range(3))
        dx, dz = DIRECTIONS[window['facing']]
        for _ in range(turns): dx, dz = -dz, dx
        tx, tz = -dz, dx
        width = hi[2 if dx else 0] - lo[2 if dx else 0] + 1
        samples = []
        ray_counts = {'clear':0,'blocked':0,'unknown':0}
        blocked = set()
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    for side in (-1, 0, 1):
                        ray = [(x + (dx + tx * side) * step, y,
                                z + (dz + tz * side) * step) for step in range(1, 4)]
                        obstruction = next((p for p in ray if p in voxels and not transparent(voxels[p])), None)
                        unknown = [p for p in ray if p not in voxels]
                        status = 'blocked' if obstruction else 'unknown' if unknown else 'clear'
                        ray_counts[status]+=1
                        sample = {'origin': [x, y, z], 'side': side, 'status': status}
                        if obstruction:
                            sample.update({'first_block': list(obstruction), 'state': voxels[obstruction]})
                            blocked.add(obstruction)
                        if unknown: sample['unspecified'] = [list(p) for p in unknown]
                        # Full counts use every glass cell. Coordinates are
                        # examples; the complete geometry remains in the archive.
                        if len(samples)<12: samples.append(sample)
        metric = {'name': window.get('name'), 'room': window['room'], 'width_cells': width,
                  'height_cells': hi[1] - lo[1] + 1,
                  'ray_counts': ray_counts,
                  'total_rays':sum(ray_counts.values()),
                  'samples_are_examples':sum(ray_counts.values())>len(samples),
                  'ray_scope': 'Three discrete directions: normal and two 45-degree diagonals, three cells deep; not a continuous field-of-view simulation.',
                  'samples': samples}
        windows.append(metric)
        if width == 1:
            finding('NARROW_WINDOW', 'review', window.get('name'),
                    'One-cell opening: evaluate the whole window group, room use and facade rhythm; a narrow opening alone does not prove a bad design.', [lo])
        if blocked:
            finding('WINDOW_SIDE_OBSTRUCTION', 'review', window.get('name'),
                    'Nearby geometry blocks sampled window views. Review column placement and window grouping instead of checking only the normal ray.', sorted(blocked))

    for room in compiled['summary'].get('rooms', []):
        lo, hi = room['from'], room['to']
        holes, unknown = [], []
        for x in range(lo[0], hi[0] + 1):
            for z in range(lo[2], hi[2] + 1):
                p = x, lo[1] - 1, z
                if p not in voxels: unknown.append(p)
                elif voxels[p].split('[', 1)[0] in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'):
                    holes.append(p)
        floors.append({'room': room['name'], 'explicit_air_below_footprint': len(holes),
                       'unspecified_below_footprint': len(unknown)})
        if holes:
            finding('ROOM_FLOOR_VOID', 'blocking', room['name'],
                    'Declared indoor footprint contains explicit air at floor height; define the supported room footprint correctly before construction.', holes)
        if unknown:
            finding('ROOM_FLOOR_UNSPECIFIED', 'review', room['name'],
                    'Floor support is absent from this design. Consult the exact survey instead of assuming existing terrain is suitable.', unknown)

    unstable = []
    for (x, y, z), state in voxels.items():
        if state.split('[', 1)[0] != 'minecraft:grass_block': continue
        above = voxels.get((x, y + 1, z), '').split('[', 1)[0]
        if above.endswith(('_log', '_wood')): unstable.append((x, y, z))
    if unstable:
        finding('TREE_BASE_GRASS', 'blocking', 'planting bases',
                'Grass directly below a solid trunk is unstable in the real world; choose a suitable stable planting base.', sorted(unstable))
    # Covered, supported floor outside the declared program is a useful sign
    # of leftover galleries or side pockets. Unspecified headroom stays unknown.
    assigned = [(tuple(r['from']), tuple(r['to'])) for r in compiled['summary'].get('rooms', [])]
    site = blueprint.get('site_layout') or {}
    for space in site.get('spaces', []):
        pa, pb = world(space['from']), world(space['to'])
        assigned.append((tuple(min(pa[i], pb[i]) for i in range(3)),
                         tuple(max(pa[i], pb[i]) for i in range(3))))
    foot_levels = {a[1] for a, _ in assigned}

    def passable(state):
        return state == 'minecraft:air' or bool(state and ('_door[' in state or state.split('[', 1)[0].endswith('_carpet')))

    def on_route(point):
        x, y, z = (point[i] - origin[i] for i in range(3))
        for _ in range(turns): x, z = z, -x
        for route in site.get('routes', []):
            for a, b in zip(route['path'], route['path'][1:]):
                axis, other = (0, 2) if a[0] != b[0] else (2, 0)
                p = (x, y, z)
                if (min(a[axis], b[axis]) <= p[axis] <= max(a[axis], b[axis])
                        and a[other] <= p[other] < a[other] + route['width']
                        and y == a[1] + (b[1] - a[1]) * (p[axis] - a[axis]) / (b[axis] - a[axis])):
                    return True
        return False

    leftovers = {}
    ceiling = compiled['summary']['bounds']['to'][1]
    for (x, floor_y, z), state in voxels.items():
        y = floor_y + 1
        if y not in foot_levels or passable(state) or state.split('[', 1)[0] in {'minecraft:water', 'minecraft:lava'}: continue
        p = x, y, z
        if any(a[1] == y and a[0] <= x <= b[0] and a[2] <= z <= b[2] for a, b in assigned) or on_route(p): continue
        headroom = [voxels.get(p), voxels.get((x, y + 1, z))]
        if any(s is not None and not passable(s) for s in headroom): continue
        if not any(voxels.get((x, top, z)) not in (None, 'minecraft:air') for top in range(y + 2, ceiling + 1)): continue
        leftovers[p] = 'unknown' if None in headroom else 'explicitly_clear'
    pockets = []
    remaining = set(leftovers)
    while remaining:
        seed = remaining.pop(); component = {seed}; pending = [seed]
        while pending:
            x, y, z = pending.pop()
            for other in ((x - 1, y, z), (x + 1, y, z), (x, y, z - 1), (x, y, z + 1)):
                if other in remaining:
                    remaining.remove(other); component.add(other); pending.append(other)
        if len(component) < 2: continue
        points = sorted(component)
        pockets.append({'from': [min(p[i] for p in points) for i in range(3)],
                        'to': [max(p[i] for p in points) for i in range(3)],
                        'floor_cells': len(points),
                        'unspecified_headroom_cells': sum(leftovers[p] == 'unknown' for p in points)})
        finding('UNPROGRAMMED_COVERED_SPACE', 'review', 'whole-building layout',
                'Covered supported floor has no declared room, outdoor function or route. Examine its purpose, dimensions and connections; do not justify an unusable leftover gap by giving it a name.', points)
    return {'blueprint_id': compiled['id'], 'review_version': REVIEW_VERSION,
            'scope': 'Saved target geometry only, not a live-world observation or an aesthetic acceptance.',
            'blocking_count': sum(f['severity'] == 'blocking' for f in findings),
            'review_count': sum(f['severity'] == 'review' for f in findings),
            'findings': findings, 'windows': windows, 'floors': floors,
            'space_program': compiled['summary'].get('site_layout'), 'unprogrammed_pockets': pockets,
            'next_step': 'Correct blocking findings; evaluate review findings against the requested style and function, then verify the actual world and inspect game visuals.'}
