"""Validate a building's space program and physical connections.

Rooms remain the indoor geometry declarations. Site spaces describe outdoor
functions; named routes must connect the program through actual clear openings.
"""
from collections import deque

PURPOSES = {'entry', 'courtyard', 'veranda', 'terrace', 'garden', 'utility'}


def validate_site_layout(layout, rooms, voxels, world_point, passable):
    if not isinstance(layout, dict): raise ValueError('site_layout must declare spaces and routes')
    spaces, routes = layout.get('spaces'), layout.get('routes')
    if not isinstance(spaces, list) or not 1 <= len(spaces) <= 48:
        raise ValueError('site_layout needs 1..48 functional spaces')
    if not isinstance(routes, list) or not 1 <= len(routes) <= 96:
        raise ValueError('site_layout needs 1..96 physical connection routes')
    nodes, entries, graph = {}, set(), {}

    def box(item):
        a, b = world_point(item['from']), world_point(item['to'])
        return tuple(min(a[i], b[i]) for i in range(3)), tuple(max(a[i], b[i]) for i in range(3))

    for room in rooms:
        nodes[room['name']] = box(room)
    for space in spaces:
        name = space.get('name')
        if not isinstance(name, str) or not name.strip() or name in nodes:
            raise ValueError('Site space names must be nonempty and distinct from room names')
        if space.get('purpose') not in PURPOSES or not str(space.get('design_intent', '')).strip():
            raise ValueError('Every site space needs an actual purpose and design_intent; naming a leftover gap is insufficient')
        a, b = box(space)
        if a[1] != b[1]: raise ValueError('Site space from/to must describe its horizontal foot-level footprint')
        for room_name, (ra, rb) in nodes.items():
            if room_name in {r['name'] for r in rooms} and a[1] == ra[1] and a[0] <= rb[0] and b[0] >= ra[0] and a[2] <= rb[2] and b[2] >= ra[2]:
                raise ValueError('Outdoor site spaces cannot cover an indoor room footprint: ' + room_name)
        nodes[name] = a, b
        if space['purpose'] == 'entry': entries.add(name)
    if not entries: raise ValueError('The space program requires an entry space')
    graph = {name: set() for name in nodes}
    all_route_cells, metrics, route_names = set(), [], set()
    room_routes = {room['name']: set() for room in rooms}

    def touches(point, name):
        lo, hi = nodes[name]
        if point[1] != lo[1]: return False
        if name in room_routes:
            inside = lo[0] <= point[0] <= hi[0] and lo[2] <= point[2] <= hi[2]
            wall = ((point[0] in (lo[0] - 1, hi[0] + 1) and lo[2] <= point[2] <= hi[2])
                    or (point[2] in (lo[2] - 1, hi[2] + 1) and lo[0] <= point[0] <= hi[0]))
            return inside or (wall and passable(voxels.get(point))
                              and passable(voxels.get((point[0],point[1]+1,point[2]))))
        return lo[0] - 1 <= point[0] <= hi[0] + 1 and lo[2] - 1 <= point[2] <= hi[2] + 1

    for route in routes:
        name, source, target = route.get('name'), route.get('from_space'), route.get('to_space')
        if not isinstance(name, str) or not name or name in route_names:
            raise ValueError('Routes need distinct names')
        route_names.add(name)
        if source == target or source not in nodes or target not in nodes:
            raise ValueError('Every route must connect two declared, different spaces or rooms')
        width = route.get('width')
        if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 16:
            raise ValueError('Route width must be an integer from 1 to 16')
        purpose = route.get('purpose')
        if purpose not in {'entry', 'main', 'secondary', 'service'}:
            raise ValueError('Route purpose must be entry/main/secondary/service')
        if purpose in {'entry', 'main'} and width < 2:
            raise ValueError('Entry/main connections must be at least two cells wide')
        vertices = route.get('path')
        if not isinstance(vertices, list) or not 2 <= len(vertices) <= 64:
            raise ValueError('A physical route needs 2..64 path vertices')
        path = [world_point(p) for p in vertices]
        if not touches(path[0], source) or not touches(path[-1], target):
            raise ValueError('Route endpoints must touch their declared source and destination at the correct foot height')
        cells = set()
        for a, b in zip(vertices, vertices[1:]):
            dx, dy, dz = (b[i] - a[i] for i in range(3))
            if (dx and dz) or not (dx or dz):
                raise ValueError('Route segments must be horizontal-axis aligned; use vertices for turns and stair flights')
            length = abs(dx or dz)
            if length > 128 or dy not in (0, length, -length):
                raise ValueError('Route stairs must change by at most one height per horizontal step')
            sx, sz, sy = (1 if dx > 0 else -1 if dx else 0), (1 if dz > 0 else -1 if dz else 0), dy // length
            # Width follows the positive relative axis, then rotates with the
            # blueprint; reversing travel cannot shift the strip to its other side.
            ox, oz = (0, 1) if dx else (1, 0)
            for step in range(length + 1):
                for offset in range(width):
                    cells.add(world_point([a[0] + sx * step + ox * offset, a[1] + sy * step,
                                          a[2] + sz * step + oz * offset]))
        for x, y, z in sorted(cells):
            if not passable(voxels.get((x, y, z))) or not passable(voxels.get((x, y + 1, z))):
                raise ValueError("Route '" + name + "' crosses a solid wall, obstruction or unspecified two-cell clearance at " + str([x, y, z]))
            support = voxels.get((x, y - 1, z))
            if not support or passable(support) or support.split('[', 1)[0] in {'minecraft:water', 'minecraft:lava'}:
                raise ValueError("Route '" + name + "' lacks an explicit stable walking surface at " + str([x, y - 1, z]))
        graph[source].add(target); graph[target].add(source)
        all_route_cells.update(cells)
        for node in (source, target):
            if node in room_routes: room_routes[node].update(cells)
        metrics.append({'name': name, 'from_space': source, 'to_space': target,
                        'purpose': purpose, 'width': width, 'verified_cells': len(cells),
                        'path_world': [list(p) for p in path]})
    reachable = set(entries)
    queue = deque(entries)
    while queue:
        for other in graph[queue.popleft()]:
            if other not in reachable: reachable.add(other); queue.append(other)
    if reachable != set(nodes):
        raise ValueError('Spaces or rooms disconnected from the entry: ' + ', '.join(sorted(set(nodes) - reachable)))

    openings = []
    for room in rooms:
        lo, hi = box(room)
        boundary = {(x, lo[1], z) for x in range(lo[0], hi[0] + 1) for z in (lo[2] - 1, hi[2] + 1)}
        boundary.update((x, lo[1], z) for z in range(lo[2], hi[2] + 1) for x in (lo[0] - 1, hi[0] + 1))
        for p in sorted(boundary):
            if passable(voxels.get(p)) and passable(voxels.get((p[0], p[1] + 1, p[2]))):
                if p not in room_routes[room['name']]:
                    raise ValueError("Room '" + room['name'] + "' has an unplanned wall opening at " + str(list(p)) + '; declare a functional connection or restore the wall')
                openings.append({'room': room['name'], 'at': list(p)})
    return {'validation_version': 1, 'spaces': spaces, 'routes': metrics,
            'entry_spaces': sorted(entries), 'reachable_nodes': sorted(reachable),
            'wall_openings': openings, 'functional_connections_verified': True}
