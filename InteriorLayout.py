"""Validate declared furniture scale and functional access in final geometry.

This is a functional design check, not a substitute for visual acceptance.
Coordinates are blueprint-relative; all checks use the compiled final voxels.
"""
from collections import deque

INTERIOR_VALIDATION_VERSION = 2


# width, depth, height maxima in the furniture's own facing coordinate system.
FURNITURE_LIMITS = {
    "sofa": (5,2,2), "armchair": (2,2,2), "chair": (1,1,2),
    "coffee_table": (3,2,1), "dining_table": (4,2,2), "desk": (3,1,2),
    "counter": (6,1,1), "cabinet": (4,1,3), "bookcase": (4,1,3),
    "bed": (2,2,1), "tv": (4,1,2), "plant": (2,2,3), "rug": (6,5,1),
}


def validate_interiors(interiors, declared_rooms, voxels, world_point, passable, turns, windows=None, version=INTERIOR_VALIDATION_VERSION):
    if type(version) is not int or version not in (1, INTERIOR_VALIDATION_VERSION):
        raise ValueError("Unsupported historical interior validation version")
    def thin_surface_height(state, y):
        block, _, properties = state.partition("[")
        props = dict(pair.split("=",1) for pair in properties.rstrip("]").split(",") if "=" in pair)
        if block.endswith("_slab") and props.get("type","bottom") != "double":
            return y + (1 if props.get("type","bottom")=="top" else 0.5)
        if block.endswith("_trapdoor") and props.get("open","false")=="false":
            return y + (1 if props.get("half","bottom")=="top" else 0.1875)
        if block.endswith("_pressure_plate"):
            return y + 0.0625
        return None
    if not isinstance(interiors,list) or len(interiors)>24:
        raise ValueError("interiors must be a list with at most 24 room layouts")
    rooms = {}
    for room in declared_rooms:
        a,b=world_point(room["from"]),world_point(room["to"])
        bounds=tuple(min(a[i],b[i]) for i in range(3)),tuple(max(a[i],b[i]) for i in range(3))
        if room["name"] in rooms and rooms[room["name"]]["bounds"]!=bounds:
            raise ValueError("Interior room names must identify one unambiguous room")
        rooms[room["name"]]={"bounds":bounds,"entry":world_point(room["entry"])}
    metrics=[]
    seen_rooms=set()
    directions={"north":(0,-1),"east":(1,0),"south":(0,1),"west":(-1,0)}
    for interior in interiors:
        name=interior.get("room")
        if name not in rooms or name in seen_rooms:
            raise ValueError("Each interior must identify a distinct declared room")
        seen_rooms.add(name)
        room=rooms[name]; lo,hi=room["bounds"]; entry=room["entry"]
        def clear(p):
            return passable(voxels.get(p)) and passable(voxels.get((p[0],p[1]+1,p[2])))
        reachable={entry}; queue=deque([entry])
        while queue:
            x,y,z=queue.popleft()
            for dx,dz in directions.values():
                p=x+dx,y,z+dz
                if p not in reachable and lo[0]-1<=p[0]<=hi[0]+1 and lo[2]-1<=p[2]<=hi[2]+1 and clear(p):
                    reachable.add(p); queue.append(p)
        def bounds(item):
            a,b=world_point(item["from"]),world_point(item["to"])
            lower=tuple(min(a[i],b[i]) for i in range(3)); upper=tuple(max(a[i],b[i]) for i in range(3))
            if any(lower[i]<lo[i] or upper[i]>hi[i] for i in range(3)):
                raise ValueError("Interior component must stay inside room '"+name+"'")
            return lower,upper
        furniture=interior.get("furniture",[])
        circulation=interior.get("circulation",[])
        zones=interior.get("zones",[])
        if not isinstance(furniture,list) or not furniture or len(furniture)>128:
            raise ValueError("Interior needs 1..128 declared furniture items")
        if not isinstance(circulation,list) or not circulation or len(circulation)>32:
            raise ValueError("Interior needs explicit main circulation rectangles")
        if not isinstance(zones,list) or not zones or len(zones)>24:
            raise ValueError("Interior needs explicit functional zones")
        zone_names=set()
        zone_rectangles={}
        zone_purposes={}
        for zone in zones:
            label=zone.get("name")
            if not isinstance(label,str) or not label.strip() or label in zone_names or not zone.get("purpose"):
                raise ValueError("Functional zones need distinct names and a purpose")
            a,b=bounds(zone)
            if a[1]!=lo[1] or b[1]!=lo[1]: raise ValueError("Zone bounds must use the room foot height")
            zone_names.add(label); zone_rectangles[label]=(a,b)
            zone_purposes[label]=zone["purpose"]
        items={}
        for item in furniture:
            label=item.get("name"); kind=item.get("type"); facing=item.get("facing")
            if not isinstance(label,str) or not label.strip() or label in items or kind not in FURNITURE_LIMITS or facing not in directions:
                raise ValueError("Furniture needs a unique name, supported type and cardinal facing")
            a,b=bounds(item)
            if kind!="tv" and a[1]!=lo[1]: raise ValueError("Floor furniture must rest at the room foot height")
            if kind=="tv" and a[1]>lo[1]+2: raise ValueError("TV mounting height is too high")
            spans=tuple(b[i]-a[i]+1 for i in range(3))
            dx,dz=directions[facing]
            for _ in range(turns): dx,dz=-dz,dx
            width,depth=(spans[0],spans[2]) if dx==0 else (spans[2],spans[0])
            limits=FURNITURE_LIMITS[kind]
            if width>limits[0] or depth>limits[1] or spans[1]>limits[2]:
                raise ValueError("Furniture '"+label+"' is oversized for "+kind+": "+str((width,depth,spans[1]))+" exceeds "+str(limits))
            if kind=="bed" and depth!=2: raise ValueError("A bed needs its actual two-cell length")
            if kind=="bed":
                bed_cells=[]
                head_cells=[]
                for x in range(a[0],b[0]+1):
                    for z in range(a[2],b[2]+1):
                        state=voxels.get((x,a[1],z),"")
                        if version>=2 and "_stairs[" in state and "half=top" in state:
                            raise ValueError("Upside-down stairs do not provide a normal chair/sofa seat")
                        if "_bed[" not in state: raise ValueError("Bed declaration must contain real bed head/foot blocks")
                        actual_facing=next(key for key,value in directions.items() if value==(dx,dz))
                        if "facing="+actual_facing not in state: raise ValueError("Bed facing declaration differs from its actual block orientation")
                        bed_cells.append((x,a[1],z))
                        if "part=head" in state: head_cells.append((x,a[1],z))
                if len(head_cells)*2!=len(bed_cells): raise ValueError("Bed declaration must include complete bed pairs")
                for x,y,z in head_cells:
                    for height in (y,y+1):
                        support=voxels.get((x+dx,height,z+dz),"")
                        if not support or passable(support) or "glass" in support:
                            raise ValueError("Bed '"+label+"' needs a two-cell headboard or solid wall behind its head, not a window or an isolated floating position")
            if kind in {"sofa","armchair","chair"}:
                usable_seats=0
                back_direction=next(key for key,value in directions.items() if value==(-dx,-dz))
                for x in range(a[0],b[0]+1):
                    for z in range(a[2],b[2]+1):
                        front=(x==b[0] if dx>0 else x==a[0] if dx<0 else z==b[2] if dz>0 else z==a[2])
                        if not front: continue
                        state=voxels.get((x,a[1],z),"")
                        if "_stairs[" in state and "facing="+back_direction not in state:
                            raise ValueError("Chair/sofa stairs must put the raised back opposite the furniture front")
                        if ("_stairs[" in state or "_slab[" in state) and passable(voxels.get((x,a[1]+1,z))): usable_seats+=1
                if usable_seats<(2 if kind=="sofa" else 1):
                    raise ValueError("Seating needs low stair/slab seats with open space above, not full-cube masses")
            if kind in {"coffee_table","dining_table","desk"}:
                tabletop=[voxels.get((x,b[1],z),"") for x in range(a[0],b[0]+1) for z in range(a[2],b[2]+1)]
                if version==1 and not any("_slab[" in s or "_trapdoor[" in s for s in tabletop):
                    raise ValueError("Tables/desks need a thin slab/trapdoor tabletop instead of a solid block slab")
                if version>=2:
                    surfaces=[thin_surface_height(s,b[1]) for s in tabletop if s and s!="minecraft:air"]
                    if not surfaces or any(height is None for height in surfaces):
                        raise ValueError("Tables/desks need a thin horizontal tabletop across every occupied top cell, not solid cubes or open vertical trapdoors")
            za,zb=zone_rectangles.get(item.get("zone"),(None,None))
            if za is None or a[0]<za[0] or b[0]>zb[0] or a[2]<za[2] or b[2]>zb[2]:
                raise ValueError("Furniture '"+label+"' must belong to its declared functional zone")
            expected_purpose={"bed":"bedroom","dining_table":"dining","desk":"study","counter":"kitchen"}.get(kind)
            if expected_purpose and zone_purposes[item["zone"]]!=expected_purpose:
                raise ValueError("Furniture '"+label+"' belongs in a "+expected_purpose+" functional zone")
            occupied=0
            for x in range(a[0],b[0]+1):
                for y in range(a[1],b[1]+1):
                    for z in range(a[2],b[2]+1):
                        state=voxels.get((x,y,z))
                        if state and state!="minecraft:air": occupied+=1
            if not occupied: raise ValueError("Furniture '"+label+"' has no actual blocks in the final layout")
            access=item.get("access",[])
            if not isinstance(access,list): raise ValueError("Furniture access must be a coordinate list")
            access_points=set()
            bed_side_points=set()
            for value in access:
                p=world_point(value)
                dist=max(a[0]-p[0],0,p[0]-b[0])+max(a[2]-p[2],0,p[2]-b[2])
                if p[1]!=lo[1] or not(lo[0]<=p[0]<=hi[0] and lo[2]<=p[2]<=hi[2]) or dist!=1 or p not in reachable or not clear(p):
                    raise ValueError("Furniture '"+label+"' access must be adjacent, clear and reachable from the entrance")
                if kind=="bed":
                    side=(a[0]<=p[0]<=b[0] and p[2] in (a[2]-1,b[2]+1)) if dx else (a[2]<=p[2]<=b[2] and p[0] in (a[0]-1,b[0]+1))
                    if side: bed_side_points.add(p)
                access_points.add(p)
            required=0 if kind in {"rug","plant","tv"} else 2 if kind=="bed" else 1
            if len(access_points)<required: raise ValueError("Furniture '"+label+"' lacks usable access positions")
            if kind=="bed" and not bed_side_points:
                raise ValueError("Bed access must include a position beside its mattress, not only at its head or foot")
            items[label]={"bounds":(a,b),"direction":(dx,dz),"source":item}
        for label,item in items.items():
            target=item["source"].get("faces")
            if version>=2 and item["source"]["type"]=="chair":
                zone=item["source"]["zone"]
                table_kind={"study":"desk","dining":"dining_table"}.get(zone_purposes[zone])
                if table_kind:
                    a,b=item["bounds"]; dx,dz=item["direction"]
                    def target_distance(candidate):
                        ca,cb=items[candidate]["bounds"]
                        return abs(ca[0]+cb[0]-a[0]-b[0])+abs(ca[2]+cb[2]-a[2]-b[2])
                    candidates=[key for key,value in items.items() if value["source"]["zone"]==zone and value["source"]["type"]==table_kind]
                    if target:
                        if target not in candidates: raise ValueError("Work/dining chairs must face their corresponding table in the same functional zone")
                    elif candidates:
                        target=min(candidates,key=target_distance)
                    else: raise ValueError("Work/dining chairs need a corresponding table in their functional zone")
                    ta,tb=items[target]["bounds"]
                    state=voxels[(a[0],a[1],a[2])]
                    seat_height=thin_surface_height(state,a[1])
                    if "_stairs[" in state:
                        if "half=top" in state: raise ValueError("Upside-down stairs do not provide a normal chair seat")
                        seat_height=a[1]+0.5
                    surfaces=[thin_surface_height(voxels.get((x,tb[1],z),""),tb[1]) for x in range(ta[0],tb[0]+1) for z in range(ta[2],tb[2]+1)]
                    surfaces=[height for height in surfaces if height is not None]
                    if seat_height is None or not surfaces or min(surfaces)<seat_height+0.25 or max(surfaces)>seat_height+1:
                        raise ValueError("Table '"+target+"' must be 0.25..1 cell above chair '"+label+"' seat; bottom slabs at the same foot height form an unusably low work/dining surface")
            if not target: continue
            if target==label or target not in items: raise ValueError("Furniture faces must identify another item in the same room")
            a,b=item["bounds"]; ta,tb=items[target]["bounds"]; dx,dz=item["direction"]
            toward=((ta[0]+tb[0]-a[0]-b[0])*dx+(ta[2]+tb[2]-a[2]-b[2])*dz)
            if toward<=0: raise ValueError("Furniture '"+label+"' faces away from its declared focus '"+target+"'")
        route_cells=set()
        for route in circulation:
            a,b=bounds(route)
            if a[1]!=lo[1] or b[1]!=lo[1] or min(b[0]-a[0]+1,b[2]-a[2]+1)<2:
                raise ValueError("Main circulation must be at least two cells wide at the room foot height")
            for x in range(a[0],b[0]+1):
                for z in range(a[2],b[2]+1):
                    p=x,lo[1],z
                    if p not in reachable or not clear(p): raise ValueError("Furniture blocks declared main circulation in room '"+name+"'")
                    route_cells.add(p)
        metrics.append({"room":name,"zones":len(zones),"furniture_items":len(items),
                        "main_circulation_cells":len(route_cells),"functional_access_verified":True,
                        "visual_grade":"requires in-game inspection"})
    if seen_rooms!=set(rooms): raise ValueError("Every declared indoor room needs an interior layout")
    window_metrics=[]
    if windows is not None:
        if not isinstance(windows,list) or len(windows)>128: raise ValueError("windows must be a bounded declaration list")
        for window in windows:
            name=window.get("room"); facing=window.get("facing")
            if name not in rooms or facing not in directions: raise ValueError("Window needs a declared room and outward cardinal facing")
            a,b=world_point(window["from"]),world_point(window["to"])
            lower=tuple(min(a[i],b[i]) for i in range(3)); upper=tuple(max(a[i],b[i]) for i in range(3))
            lo,hi=rooms[name]["bounds"]; dx,dz=directions[facing]
            for _ in range(turns): dx,dz=-dz,dx
            axis=0 if dx else 2
            edge=hi[axis]+1 if (dx or dz)>0 else lo[axis]-1
            if lower[axis]!=edge or upper[axis]!=edge or lower[1]<lo[1] or upper[1]>hi[1]:
                raise ValueError("Window must be on its room's exterior boundary, facing outwards")
            other=2 if axis==0 else 0
            if lower[other]<lo[other] or upper[other]>hi[other]: raise ValueError("Window spans outside its room")
            total=0; clear_view=0; clear_inside=0
            for x in range(lower[0],upper[0]+1):
                for y in range(lower[1],upper[1]+1):
                    for z in range(lower[2],upper[2]+1):
                        state=voxels.get((x,y,z),"")
                        if "glass" not in state.split("[",1)[0]: raise ValueError("Window declaration includes a non-glass block")
                        total+=1
                        ray=[voxels.get((x+dx*n,y,z+dz*n)) for n in range(1,4)]
                        if all(s and (s=="minecraft:air" or "glass" in s or "leaves" in s or "_fence" in s or "chain" in s) for s in ray): clear_view+=1
                        inside=voxels.get((x-dx,y,z-dz),"")
                        if inside=="minecraft:air" or "glass" in inside or inside.split("[",1)[0].endswith("_carpet"):
                            clear_inside+=1
            if clear_view<total*0.8:
                raise ValueError("Window '"+str(window.get("name"))+"' has a blocked or unspecified exterior view: "+str(clear_view)+"/"+str(total)+" clear three-cell rays. Check adjacent rooms/walls; declare surveyed exterior clearance without erasing existing buildings.")
            if clear_inside<total*0.8:
                raise ValueError("Window '"+str(window.get("name"))+"' is blocked from inside by furniture or a wall: "+str(clear_inside)+"/"+str(total)+" clear inner cells. Keep tall cabinets and bookcases away from the glass.")
            window_metrics.append({"room":name,"name":window.get("name"),"glass_cells":total,"clear_view_cells":clear_view})
        covered=set()
        for window in windows:
            a,b=world_point(window["from"]),world_point(window["to"])
            for x in range(min(a[0],b[0]),max(a[0],b[0])+1):
                for y in range(min(a[1],b[1]),max(a[1],b[1])+1):
                    for z in range(min(a[2],b[2]),max(a[2],b[2])+1): covered.add((window["room"],x,y,z))
        for name in seen_rooms:
            if not any(w["room"]==name for w in window_metrics): raise ValueError("Habitable room '"+name+"' needs a verified outward window")
            lo,hi=rooms[name]["bounds"]
            boundary=set()
            for y in range(lo[1],hi[1]+1):
                for x in range(lo[0],hi[0]+1):
                    boundary.add((x,y,lo[2]-1)); boundary.add((x,y,hi[2]+1))
                for z in range(lo[2],hi[2]+1):
                    boundary.add((lo[0]-1,y,z)); boundary.add((hi[0]+1,y,z))
            unclaimed=[p for p in boundary if "glass" in voxels.get(p,"").split("[",1)[0] and (name,*p) not in covered]
            if unclaimed: raise ValueError("Room '"+name+"' has undeclared glass openings; declare every boundary glass cell, including those facing adjacent walls. First: "+str(list(sorted(unclaimed)[0])))
    return {"rooms":metrics,"windows":window_metrics}
