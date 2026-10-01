"""Declarative, bounded Minecraft 1.16.5 architecture compiler; no game writes.

All coordinates are relative to origin. Operations are applied in order.
The exact final voxel map is the specification, including intentional air.
"""
import copy
import hashlib
import json
import re
from collections import Counter
from collections import deque
from pathlib import Path
from functools import lru_cache

MAX_CELLS = 262144
MAX_OPERATIONS = 4096
STATE_RE = re.compile(r"minecraft:[a-z0-9_]+(?:\[[a-z0-9_=,]+\])?\Z")


@lru_cache(maxsize=1)
def block_catalog():
    path = Path(__file__).with_name("block-catalog-1.16.5.json")
    if not path.exists():
        raise ValueError("Missing 1.16.5 block catalog; run gradlew architectureChecks before building")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_state(value):
    if not isinstance(value, str):
        raise ValueError("Block state must be a string")
    if not value.startswith("minecraft:") and re.fullmatch(r"[a-z0-9_]+(?:\[[a-z0-9_=,]+\])?", value):
        value = "minecraft:" + value
    if not STATE_RE.fullmatch(value):
        raise ValueError("Invalid block state syntax: " + value)
    block = value.split("[", 1)[0]
    catalog = block_catalog()
    if block not in catalog:
        raise ValueError("Not a Minecraft 1.16.5 block or declared palette role: " + block)
    if block.endswith("_leaves") and "persistent=" not in value:
        value = value[:-1] + ",persistent=true]" if "[" in value else value + "[persistent=true]"
    if "[" in value:
        seen = set()
        for pair in value[:-1].split("[", 1)[1].split(","):
            if pair.count("=") != 1: raise ValueError("Invalid property: " + pair)
            key, property_value = pair.split("=")
            if key in seen or property_value not in catalog[block].get(key, []):
                raise ValueError("Invalid or duplicate 1.16.5 block property: " + block + " " + pair)
            seen.add(key)
    return value


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Coordinates, sizes and counts must be integers")
    return value


def vector(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Expected [x,y,z]")
    return tuple(integer(v) for v in value)


def rotate_state(state, turns):
    if "[" not in state or not turns:
        return state
    block, props = state[:-1].split("[", 1)
    props = dict(p.split("=", 1) for p in props.split(","))
    directions = ["north", "east", "south", "west"]
    for key in ("facing",):
        if props.get(key) in directions:
            props[key] = directions[(directions.index(props[key]) + turns) % 4]
    if turns % 2 and props.get("axis") in ("x", "z"):
        props["axis"] = "z" if props["axis"] == "x" else "x"
    # Explicit fence/wall/pane directional properties must rotate too.
    directional = {k: props.pop(k) for k in directions if k in props}
    for key, value in directional.items():
        props[directions[(directions.index(key) + turns) % 4]] = value
    return block + "[" + ",".join(k + "=" + str(v) for k, v in sorted(props.items())) + "]"


def structural_state(state):
    """Keep exact geometry properties; omit interactive door/bed state only.

    Trapdoor open remains structural because it changes the tabletop plane.
    The original specification and survey always retain every actual property.
    """
    block,separator,properties=state.partition("[")
    if not separator: return state
    props=dict(pair.split("=",1) for pair in properties.rstrip("]").split(","))
    volatile={"open","powered"} if block.endswith("_door") else {"occupied"} if block.endswith("_bed") else set()
    props={key:value for key,value in props.items() if key not in volatile}
    return block+("["+",".join(key+"="+value for key,value in sorted(props.items()))+"]" if props else "")


def compile_blueprint(blueprint, *, interior_validation_version=None):
    if not isinstance(blueprint, dict):
        raise ValueError("Blueprint must be an object")
    for field in ("name", "style", "design_intent"):
        if not isinstance(blueprint.get(field), str) or not blueprint[field].strip():
            raise ValueError("Blueprint requires " + field)
    origin = vector(blueprint.get("origin"))
    orientation = blueprint.get("orientation", "north")
    if orientation not in ("north", "east", "south", "west"):
        raise ValueError("orientation must be north/east/south/west")
    turns = ("north", "east", "south", "west").index(orientation)
    palette = blueprint.get("palette", {})
    if not isinstance(palette, dict) or not palette:
        raise ValueError("Define a semantic palette of Minecraft block states")
    palette = {name: normalize_state(value) for name, value in palette.items()}
    operations = []
    automatic_rooms = []

    def emit(a, b, material, label):
        a, b = vector(list(a)), vector(list(b))
        lo = tuple(min(a[i], b[i]) for i in range(3))
        hi = tuple(max(a[i], b[i]) for i in range(3))
        state = normalize_state(palette.get(material, material))
        state = rotate_state(state, turns)

        def transform(p):
            x, y, z = p
            for _ in range(turns):
                x, z = -z, x
            return [x + origin[0], y + origin[1], z + origin[2]]

        a, b = transform(lo), transform(hi)
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        if lo[1] < 0 or hi[1] > 255 or max(abs(lo[0]), abs(lo[2]), abs(hi[0]), abs(hi[2])) > 29999900:
            raise ValueError("Blueprint exceeds world bounds")
        operations.append({"from": lo, "to": hi, "state": state, "label": str(label)[:80]})
        if len(operations) > MAX_OPERATIONS:
            raise ValueError("Too many expanded operations")

    def expand(items, offset=(0, 0, 0), depth=0):
        if depth > 4 or not isinstance(items, list) or len(items) > 1024:
            raise ValueError("Too many operations or repeat nesting levels")
        def point(value):
            return tuple(a + b for a, b in zip(vector(value), offset))
        for op in items:
            kind = op.get("kind")
            label = op.get("label", kind)
            required = {"repeat": ("count", "step", "operations"), "block": ("at", "material"),
                        "room": ("from", "to", "material", "entry")}.get(kind, ("from", "to", "material"))
            for field in required:
                if field not in op: raise ValueError("Operation '" + str(label) + "' kind=" + str(kind) + " requires " + field)
            if kind == "repeat":
                count = integer(op.get("count"))
                step = vector(op.get("step"))
                if count < 1 or count > 128:
                    raise ValueError("Repeat count must be 1..128")
                for n in range(count):
                    expand(op.get("operations"), tuple(offset[i] + n * step[i] for i in range(3)), depth + 1)
                continue
            if kind == "block":
                emit(point(op["at"]), point(op["at"]), op["material"], label)
                continue
            if kind in ("fill", "hollow", "room", "frame", "gable"):
                a, b = point(op["from"]), point(op["to"])
                lo = tuple(min(a[i], b[i]) for i in range(3))
                hi = tuple(max(a[i], b[i]) for i in range(3))
                material = op["material"]
                if kind == "fill":
                    emit(lo, hi, material, label)
                elif kind in ("hollow", "room"):
                    if min(hi[i] - lo[i] for i in range(3)) < 2:
                        raise ValueError("Hollow box must be at least 3x3x3")
                    emit(lo, hi, material, label)
                    emit(tuple(v + 1 for v in lo), tuple(v - 1 for v in hi), "minecraft:air", label + " interior")
                    if kind == "room":
                        if op.get("floor_material"):
                            emit(lo, (hi[0],lo[1],hi[2]), op["floor_material"], label + " floor")
                        if op.get("roof_material"):
                            emit((lo[0],hi[1],lo[2]), hi, op["roof_material"], label + " roof")
                        entry = point(op["entry"])
                        if entry[1] != lo[1]+1 or not (lo[0]<=entry[0]<=hi[0] and lo[2]<=entry[2]<=hi[2]) or not (entry[0] in (lo[0],hi[0]) or entry[2] in (lo[2],hi[2])):
                            raise ValueError("room.entry must be on the outer wall at floor_y+1")
                        emit(entry, (entry[0],entry[1]+1,entry[2]), "minecraft:air", label + " entrance")
                        automatic_rooms.append({"name": str(label), "from": [v+1 for v in lo],
                                                "to": [v-1 for v in hi], "entry": list(entry)})
                elif kind == "frame":
                    for axis in range(3):
                        others = [i for i in range(3) if i != axis]
                        for v1 in (lo[others[0]], hi[others[0]]):
                            for v2 in (lo[others[1]], hi[others[1]]):
                                p, q = list(lo), list(lo)
                                p[others[0]] = q[others[0]] = v1
                                p[others[1]] = q[others[1]] = v2
                                q[axis] = hi[axis]
                                emit(p, q, material, label)
                else:
                    # Layered pitched roof. from.y is eave height; to.y is
                    # ridge height; ridge_axis is x or z; valid for any width.
                    axis = op.get("ridge_axis", "z")
                    if axis not in ("x", "z"):
                        raise ValueError("gable ridge_axis must be x or z")
                    span = 0 if axis == "z" else 2
                    if hi[1] - lo[1] != (hi[span] - lo[span]) // 2:
                        raise ValueError("Gable ridge height must equal eave + floor(span/2)")
                    for n in range(hi[1] - lo[1] + 1):
                        p, q = list(lo), list(hi)
                        p[1] = q[1] = lo[1] + n
                        p[span] = lo[span] + n
                        q[span] = hi[span] - n
                        left = list(q); left[span] = p[span]
                        right = list(p); right[span] = q[span]
                        emit(p, left, material, label)
                        if right != left:
                            emit(right, q, material, label)
            elif kind == "line":
                a, b = point(op["from"]), point(op["to"])
                steps = max(abs(a[i] - b[i]) for i in range(3))
                if steps > 255:
                    raise ValueError("Line too long")
                for n in range(steps + 1):
                    p = tuple(round(a[i] + (b[i] - a[i]) * n / max(1, steps)) for i in range(3))
                    emit(p, p, op["material"], label)
            else:
                raise ValueError("Unknown operation kind: " + str(kind))

    expand(blueprint.get("operations"))
    if not operations:
        raise ValueError("Empty blueprint")
    lo = [min(op["from"][i] for op in operations) for i in range(3)]
    hi = [max(op["to"][i] for op in operations) for i in range(3)]
    sizes = [hi[i] - lo[i] + 1 for i in range(3)]
    if any(sizes[i] > (128, 80, 128)[i] for i in range(3)) or sizes[0] * sizes[1] * sizes[2] > MAX_CELLS:
        raise ValueError("Bounded design volume: <=128x80x128 and <=262144 cells")
    # Last write wins, so windows/doors and later decorations override walls.
    voxels = {}
    write_volume = 0
    for op in operations:
        a, b = op["from"], op["to"]
        write_volume += (b[0]-a[0]+1) * (b[1]-a[1]+1) * (b[2]-a[2]+1)
        if write_volume > 2 * MAX_CELLS:
            raise ValueError("Excessive overlapping write volume")
        for y in range(a[1], b[1] + 1):
            for z in range(a[2], b[2] + 1):
                for x in range(a[0], b[0] + 1):
                    voxels[x, y, z] = op["state"]
    counts = Counter(v.split("[", 1)[0] for v in voxels.values())
    occupied = sum(n for state, n in counts.items() if state != "minecraft:air")
    if occupied == 0:
        raise ValueError("Blueprint contains no building")
    checkpoints = blueprint.get("checkpoints", [])
    if not isinstance(checkpoints, list) or len(checkpoints) > 32:
        raise ValueError("checkpoints must be a list of at most 32 design checks")
    summary = {
        "name": blueprint["name"], "style": blueprint["style"],
        "design_intent": blueprint["design_intent"], "bounds": {"from": lo, "to": hi},
        "size": sizes, "operation_count": len(operations), "specified_cells": len(voxels),
        "occupied_blocks": occupied, "material_counts": dict(counts),
        "checkpoints": checkpoints, "coordinate_system": "world", "orientation": orientation,
    }
    room_metrics = []
    room_errors = []
    def world_point(value):
        x, y, z = vector(value)
        for _ in range(turns): x, z = -z, x
        return x + origin[0], y + origin[1], z + origin[2]
    def passable(state):
        return state == "minecraft:air" or (state and "_door[" in state) or (state and state.split("[", 1)[0].endswith("_carpet"))
    declared_rooms = blueprint.get("rooms", []) + automatic_rooms
    if len(declared_rooms)>24: raise ValueError("At most 24 room declarations per design")
    for room in declared_rooms:
        a, b = world_point(room["from"]), world_point(room["to"])
        room_lo = [min(a[i], b[i]) for i in range(3)]
        room_hi = [max(a[i], b[i]) for i in range(3)]
        if room_hi[1]-room_lo[1]<1 or any(room_lo[i]<lo[i] or room_hi[i]>hi[i] for i in range(3)):
            raise ValueError("Room interior needs >=2 blocks headroom and must lie inside blueprint bounds")
        total = (room_hi[0]-room_lo[0]+1)*(room_hi[2]-room_lo[2]+1)
        clear = 0
        for x in range(room_lo[0], room_hi[0]+1):
            for z in range(room_lo[2], room_hi[2]+1):
                if passable(voxels.get((x,room_lo[1],z))) and passable(voxels.get((x,room_lo[1]+1,z))): clear += 1
        if clear < max(1, total * 0.4):
            room_errors.append("Room '" + str(room.get("name")) + "' is solid/unspecified: only " + str(clear) + "/" + str(total) + " cells have explicit two-cell headroom; clear interior with air or hollow. Only indoor habitable spaces belong in rooms; courtyard/pool/terrace belong in checkpoints.")
            continue
        entry = world_point(room["entry"])
        if not passable(voxels.get(entry)) or not passable(voxels.get((entry[0],entry[1]+1,entry[2]))):
            room_errors.append("Room '" + str(room.get("name")) + "' entry must specify two passable cells (air or complete door) at " + str(list(entry)))
            continue
        if entry[1] != room_lo[1] or not (room_lo[0]-1 <= entry[0] <= room_hi[0]+1 and room_lo[2]-1 <= entry[2] <= room_hi[2]+1):
            room_errors.append("Room '" + str(room.get("name")) + "' entry must be adjacent to its interior at the same foot height")
            continue
        visited = {entry}
        queue = deque([entry])
        while queue:
            x, y, z = queue.popleft()
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                p = (x+dx,y,z+dz)
                if p in visited or not (room_lo[0]-1 <= p[0] <= room_hi[0]+1 and room_lo[2]-1 <= p[2] <= room_hi[2]+1): continue
                if passable(voxels.get(p)) and passable(voxels.get((p[0],y+1,p[2]))):
                    visited.add(p); queue.append(p)
        reachable = sum(room_lo[0] <= x <= room_hi[0] and room_lo[2] <= z <= room_hi[2] for x,y,z in visited)
        if reachable < max(1, total * 0.3):
            room_errors.append("Room '" + str(room.get("name")) + "' entry has no adequate connected interior: " + str(reachable) + "/" + str(total) + " reachable cells; open the actual doorway/corridor")
            continue
        room_metrics.append({"name": room["name"], "clear_footprint_cells": clear, "footprint_cells": total,
                             "reachable_footprint_cells": reachable,
                             "entry_world": list(entry), "from": room_lo, "to": room_hi})
    summary["rooms"] = room_metrics
    if room_errors: raise ValueError("; ".join(room_errors))
    paired_errors = []
    for position, state in voxels.items():
        block = state.split("[", 1)[0]
        props = dict(p.split("=", 1) for p in state[:-1].split("[", 1)[1].split(",")) if "[" in state else {}
        x, y, z = position
        if block.endswith("_door"):
            half = props.get("half", "lower")
            other = voxels.get((x, y + (1 if half == "lower" else -1), z), "")
            needed = "upper" if half == "lower" else "lower"
            if other.split("[", 1)[0] != block or "half=" + needed not in other:
                paired_errors.append("Door requires matching lower/upper half at " + str(list(position)))
        elif block.endswith("_bed"):
            facing = props.get("facing", "north")
            dx, dz = {"north":(0,-1), "south":(0,1), "west":(-1,0), "east":(1,0)}[facing]
            part = props.get("part", "foot")
            sign = 1 if part == "foot" else -1
            other = voxels.get((x+dx*sign,y,z+dz*sign), "")
            needed = "head" if part == "foot" else "foot"
            if other.split("[", 1)[0] != block or "part=" + needed not in other or "facing=" + facing not in other:
                paired_errors.append("Bed requires adjacent matching head/foot facing " + facing + " at " + str(list(position)))
        if len(paired_errors) >= 8: break
    if paired_errors: raise ValueError("; ".join(paired_errors))
    if blueprint.get("interiors"):
        from InteriorLayout import validate_interiors, INTERIOR_VALIDATION_VERSION
        version=INTERIOR_VALIDATION_VERSION if interior_validation_version is None else interior_validation_version
        summary["interiors"] = validate_interiors(blueprint["interiors"], declared_rooms, voxels, world_point, passable, turns, blueprint.get("windows"), version=version)
    if 'site_layout' in blueprint:
        from SiteLayout import validate_site_layout
        summary['site_layout']=validate_site_layout(blueprint['site_layout'],declared_rooms,voxels,world_point,passable)
    canonical = json.dumps({"operations": operations, "summary": summary}, sort_keys=True, ensure_ascii=False)
    blueprint_id = hashlib.sha256(canonical.encode()).hexdigest()[:20]
    return {"id": blueprint_id, "summary": summary, "operations": operations}, voxels


class BlueprintStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, blueprint, base_blueprint_id=None, require_interiors=False, require_site_layout=False):
        if base_blueprint_id:
            self.load(base_blueprint_id)  # Verify integrity before inheritance.
            old = json.loads((self.directory / (base_blueprint_id + ".json")).read_text(encoding="utf-8"))["blueprint"]
            if blueprint.get("origin") != old.get("origin") or blueprint.get("orientation", "north") != old.get("orientation", "north"):
                raise ValueError("A revision must preserve the base origin and orientation")
            def freeze(items):
                items = copy.deepcopy(items)
                for op in items:
                    for key in ("material", "floor_material", "roof_material"):
                        if key in op:
                            op[key] = normalize_state(old["palette"].get(op[key], op[key]))
                    if "operations" in op:
                        op["operations"] = freeze(op["operations"])
                return items
            blueprint = copy.deepcopy(blueprint)
            blueprint["operations"] = freeze(old["operations"]) + blueprint["operations"]
            inherited_rooms = {r["name"]: r for r in old.get("rooms", [])}
            inherited_rooms.update({r["name"]: r for r in blueprint.get("rooms", [])})
            blueprint["rooms"] = list(inherited_rooms.values())
            inherited_interiors = {r["room"]: r for r in old.get("interiors", [])}
            inherited_interiors.update({r["room"]: r for r in blueprint.get("interiors", [])})
            if inherited_interiors:
                blueprint["interiors"] = list(inherited_interiors.values())
            if "windows" not in blueprint and "windows" in old:
                blueprint["windows"] = copy.deepcopy(old["windows"])
            blueprint["base_blueprint_id"] = base_blueprint_id
            if 'site_layout' not in blueprint and 'site_layout' in old:
                blueprint['site_layout']=copy.deepcopy(old['site_layout'])
        compiled, voxels = compile_blueprint(blueprint)
        if require_interiors and compiled["summary"].get("rooms") and not blueprint.get("interiors"):
            raise ValueError("Habitable architectural designs require functional interior layouts and outward windows")
        if require_site_layout and compiled['summary'].get('rooms') and 'site_layout' not in blueprint:
            raise ValueError('Habitable designs require site_layout: a whole-building space program and physical connections, not isolated room checks')
        if base_blueprint_id:
            compiled["summary"]["supersedes_id"] = base_blueprint_id
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (compiled["id"] + ".json")
        from InteriorLayout import INTERIOR_VALIDATION_VERSION
        payload = {"blueprint": copy.deepcopy(blueprint), "compiled": compiled,
                   "interior_validation_version": INTERIOR_VALIDATION_VERSION}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        preview = self.directory / (compiled["id"] + ".svg")
        preview.write_text(render_preview(compiled, voxels), encoding="utf-8")
        return {"blueprint_id": compiled["id"], **compiled["summary"], "interior_validation_version":INTERIOR_VALIDATION_VERSION,
                "preview_path": str(preview.resolve())}

    def load(self, blueprint_id, *, require_current_interiors=False):
        if not re.fullmatch(r"[0-9a-f]{20}", blueprint_id):
            raise ValueError("Invalid blueprint id")
        saved = json.loads((self.directory / (blueprint_id + ".json")).read_text(encoding="utf-8"))
        # Reproduce the historical validator when reading an old design. New
        # revisions always use current rules, even when their base is historical.
        compiled, _ = compile_blueprint(saved["blueprint"], interior_validation_version=saved.get("interior_validation_version",1))
        if compiled["id"] != blueprint_id:
            raise ValueError("Blueprint integrity check failed")
        if require_current_interiors:
            if compiled["summary"].get("rooms") and not saved["blueprint"].get("interiors"):
                raise ValueError("Historical habitable designs require a current interior revision before new construction")
            compile_blueprint(saved["blueprint"])
        if saved["blueprint"].get("base_blueprint_id"):
            compiled["summary"]["supersedes_id"] = saved["blueprint"]["base_blueprint_id"]
        return compiled

    def verification_plan(self, blueprint_id):
        compiled=self.load(blueprint_id)
        operations=copy.deepcopy(compiled["operations"])
        for operation in operations: operation["state"]=structural_state(operation["state"])
        return {"blueprint_id":blueprint_id,"operations":operations,
                "target_cells":compiled["summary"]["specified_cells"],
                "verification_scope":"Complete geometry and specified structural properties; door open/powered and bed occupied are interactive state retained in exact surveys. Trapdoor open is checked."}

    def review(self, blueprint_id):
        self.load(blueprint_id)
        saved=json.loads((self.directory/(blueprint_id+'.json')).read_text(encoding='utf-8'))
        compiled,voxels=compile_blueprint(saved['blueprint'],
                         interior_validation_version=saved.get('interior_validation_version',1))
        from BuildingReview import review_design
        return review_design(saved['blueprint'],compiled,voxels)

    def execution_plan(self, blueprint_id):
        compiled=self.load(blueprint_id,require_current_interiors=True)
        source=json.loads((self.directory/(blueprint_id+".json")).read_text(encoding="utf-8"))
        base_id=source["blueprint"].get("base_blueprint_id")
        if not base_id:
            return {"blueprint_id":blueprint_id,"operations":compiled["operations"],
                    "mode":"new_build","target_cells":compiled["summary"]["specified_cells"]}
        self.load(base_id)
        base=json.loads((self.directory/(base_id+".json")).read_text(encoding="utf-8"))
        _,before=compile_blueprint(base["blueprint"],interior_validation_version=base.get("interior_validation_version",1))
        _,after=compile_blueprint(source["blueprint"],interior_validation_version=source.get("interior_validation_version",1))
        changed={point:state for point,state in after.items() if point not in before or structural_state(state)!=structural_state(before[point])}
        operations=[]
        # Air and low supports precede upper furniture. Pack equal states into
        # horizontal runs so a small renovation stays a small world mutation.
        row=None
        for (x,y,z),state in sorted(changed.items(),key=lambda value:(value[0][1],value[0][2],value[0][0])):
            if row and row["to"]==[x-1,y,z] and row["state"]==state:
                row["to"]=[x,y,z]
            else:
                row={"from":[x,y,z],"to":[x,y,z],"state":state,"label":"revision delta"}
                operations.append(row)
        if len(operations)>MAX_OPERATIONS: raise ValueError("Revision delta exceeds operation limit; divide it into phases")
        return {"blueprint_id":blueprint_id,"base_blueprint_id":base_id,"operations":operations,
                "mode":"revision_delta","changed_cells":len(changed),"target_cells":compiled["summary"]["specified_cells"]}

    def inspect(self, blueprint_id, operation_start=0, *, max_bytes):
        compiled = self.load(blueprint_id)  # Validate id, registry and integrity.
        saved = json.loads((self.directory / (blueprint_id + ".json")).read_text(encoding="utf-8"))["blueprint"]
        operations = saved["operations"]
        start = integer(operation_start)
        if start < 0 or start > len(operations):
            raise ValueError("operation_start must lie within the source operations")
        result = {"blueprint_id": blueprint_id, "summary": compiled["summary"],
                  "blueprint": {k: copy.deepcopy(v) for k, v in saved.items() if k != "operations"},
                  "operation_start": start, "operation_end_exclusive": start,
                  "total_source_operations": len(operations), "next_operation_start": None,
                  "observation": "Saved design only; re-survey to observe current world geometry."}
        from InteriorLayout import INTERIOR_VALIDATION_VERSION
        record=json.loads((self.directory / (blueprint_id + ".json")).read_text(encoding="utf-8"))
        result["interior_validation"]={"saved_version":record.get("interior_validation_version",1),
                                       "current_version":INTERIOR_VALIDATION_VERSION,
                                       "rule":"Historical checks allow reading and revision; every new design/revision must pass current checks. Neither version proves visual quality."}
        result["blueprint"]["operations"] = []
        def size():
            return len(json.dumps(json.dumps(result, ensure_ascii=False), ensure_ascii=False).encode("utf-8"))
        if size() > max_bytes:
            raise ValueError("Blueprint metadata exceeds available context capacity")
        for index in range(start, len(operations)):
            result["blueprint"]["operations"].append(copy.deepcopy(operations[index]))
            result["operation_end_exclusive"] = index+1
            result["next_operation_start"] = index+1 if index+1 < len(operations) else None
            if size() > max_bytes:
                result["blueprint"]["operations"].pop()
                result["operation_end_exclusive"] = index
                result["next_operation_start"] = index
                if index == start:
                    raise ValueError("One source operation exceeds context capacity; inspect the local archived blueprint")
                break
        return result


def render_preview(compiled, voxels):
    """Exact orthographic projections of non-air voxels, not a beauty render."""
    import html
    colors = {"quartz": "#e8e3d7", "white": "#f5f1e7", "glass": "#a9dbe5", "oak": "#98714b",
              "spruce": "#5b4130", "stone": "#8e9498", "brick": "#aa6656", "black": "#30343b",
              "water": "#438fc4", "leaf": "#568546", "leaves": "#568546", "grass": "#77a65a",
              "lantern": "#edbc65", "glowstone": "#edbc65"}
    def color(state):
        return next((c for key, c in colors.items() if key in state), "#b1a99d")
    lo = compiled["summary"]["bounds"]["from"]
    hi = compiled["summary"]["bounds"]["to"]
    scale = min(12, 340 / max(compiled["summary"]["size"]))
    shapes = []
    for view, axes, depth in (("Top (north up)", (0, 2), 1), ("Front (north)", (0, 1), 2), ("East", (2, 1), 0)):
        index = len(shapes)
        visible = {}
        for p, state in voxels.items():
            if state == "minecraft:air": continue
            key = p[axes[0]], p[axes[1]]
            priority = p[depth] if index in (0, 2) else -p[depth]
            if key not in visible or priority > visible[key][0]:
                visible[key] = priority, state
        xoff = 20 + index * 390
        items = [f'<text x="{xoff}" y="75">{view}</text>']
        for (u, v), (_, state) in visible.items():
            x = xoff + (u - lo[axes[0]]) * scale
            yy = v - lo[axes[1]] if index == 0 else hi[axes[1]] - v
            y = 90 + yy * scale
            items.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{scale:.2f}" height="{scale:.2f}" fill="{color(state)}"><title>{html.escape(state)}</title></rect>')
        shapes.append("\n".join(items))
    title = html.escape(compiled["summary"]["name"] + " | " + compiled["summary"]["style"])
    return '<svg xmlns="http://www.w3.org/2000/svg" width="1190" height="500" viewBox="0 0 1190 500"><rect width="1190" height="500" fill="#faf8f4"/><g font-family="sans-serif" font-size="16"><text x="20" y="30">' + title + '</text><text x="20" y="53">Exact blueprint projections; final world verification is separate.</text>' + "".join(shapes) + '</g></svg>'
