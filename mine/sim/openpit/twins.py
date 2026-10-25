"""Ditto things of the pit: definitions for setup and feature snapshots that the
live simulator publishes as telemetry."""

import math

from .model import EVAC_STATES, WHATIF_DEFAULTS
from .params import WASTE

NAMESPACE = "neta.pit"
POLICY = "default:basic_policy"
PIT = f"{NAMESPACE}:pit-01"
CONTROL = "control-01"
WHATIF = "whatif-01"
DUMP_TWIN = {"dump": "dump-01", "dump-2": "dump-02"}


def _length(points) -> float:
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(points, points[1:]))


def _roads_used(model, truck) -> set[str]:
    if truck.route is None:
        return set()
    pts = set(truck.route.points)
    used = {road for road, line in model.pit.roads.items() if road != "road-ramp" and line[-1] in pts}
    if any(p[2] < 0 for p in pts):
        used.add("road-ramp")
    return used

DEFAULT_CONTROL = {
    "dispatch_mode": {"value": "B"},
    "oversize_mode": {"value": "A"},
    "blast_mode": {"value": "A"},
    "speedup": {"value": 30, "unit": "x"},
    "run": {"value": 0},
}


def thing_id(name: str) -> str:
    return f"{NAMESPACE}:{name}"


def _thing(name: str, kind: str, features: dict, parent: str | None = PIT, **attributes) -> tuple[str, dict]:
    attrs = {"name": name, "_isType": False, "kind": kind, **attributes}
    if parent:
        attrs["_parents"] = parent
    return thing_id(name), {
        "policyId": POLICY,
        "attributes": attrs,
        "features": {k: {"properties": v} for k, v in features.items()},
    }


def definitions(model) -> dict:
    """Things to create in Ditto, parents first, with the t=0 features."""
    p, pit = model.p, model.pit
    modes = {k: v["value"] for k, v in DEFAULT_CONTROL.items()}
    snap = snapshot(model, modes, 0)
    things = dict([
        _thing("pit-01", "mine", snap["pit-01"], parent=None, title="Open pit NETA",
               geometry=pit.describe()),
        _thing(CONTROL, "control", DEFAULT_CONTROL),
        _thing("dispatcher-01", "service", {
            "decisions": {"value": 0}, "last_truck": {"value": ""}, "last_face": {"value": ""},
            "target_lower_tph": {"value": 0, "unit": "t/h"}, "target_upper_tph": {"value": 0, "unit": "t/h"},
        }, module="1. Haulage and dispatch"),
        _thing("crusher-guard-01", "service", {
            "decisions": {"value": 0}, "rockbreaks": {"value": 0}, "last_size_m": {"value": 0, "unit": "m"},
            "threshold_m": {"value": 0, "unit": "m"},
        }, module="2. Oversize control: camera and rock breaker"),
        _thing("blast-control-01", "service", {
            "decisions": {"value": 0}, "permits": {"value": 0}, "last_zone": {"value": ""},
            "last_decision": {"value": ""},
        }, module="3. Re-entry after blast by drone gas survey"),
        _thing("advisor-01", "service", {
            "count": {"value": 0}, "critical": {"value": 0}, "items": {"value": "[]"}, "history": {"value": "[]"},
        }, module="Shift advisor: who should do what now"),
        _thing("drone-01", "drone", snap["drone-01"], cells=p.blasting.drone_cells,
               sweep_s=p.blasting.drone_cells * p.blasting.drone_cell_s, gases=["CO", "NO2"],
               pdk_mg_m3=dict(p.blasting.pdk_mg_m3)),
        _thing("crusher-01", "crusher", snap["crusher-01"], model=p.crusher.model,
               rated_tph=p.crusher.rate_tph, feed_opening_mm=p.crusher.feed_opening_mm, position=pit.crusher),
        _thing("weighbridge-01", "weighbridge", snap["weighbridge-01"], lanes=p.site.weighbridge_lanes,
               weigh_min=list(p.site.weigh_min), position=pit.weighbridge, road="road-crusher",
               role="ore trucks are weighed here; shift ore_t is booked from the scale reading"),
        _thing("dump-01", "waste-dump", snap["dump-01"], spots=p.site.waste_dump_spots, position=pit.waste_dump,
               road="road-dump"),
        _thing("dump-02", "waste-dump", snap["dump-02"], spots=p.site.waste_dump_spots, position=pit.reserve_dump,
               road="road-dump-2", role="reserve, used while road-dump is closed"),
        _thing("workshop-01", "workshop", snap["workshop-01"], position=pit.workshop, road="road-service",
               bays=p.site.workshop_bays),
        _thing("fuel-01", "fuel-station", snap["fuel-01"], position=pit.fuel, road="road-service",
               bays=p.site.fuel_bays, fill_min=p.shift_time.fueling_min),
        _thing("dozer-01", "dozer", snap["dozer-01"], model="Cat D10T2", works_at="dump-01",
               role="pushes tipped waste over the dump crest; without it the dump takes one truck at a time"),
        _thing("grader-01", "grader", snap["grader-01"], model="Cat 24", works_on="haul roads",
               role="in rain brings rolling resistance from 4 % back towards 3 %"),
        _thing("stockpile-01", "ore-stockpile", snap["stockpile-01"], position=pit.stockpile,
               live_capacity_t=p.plant.stockpile_live_t, fed_by="crusher-01", feeds="plant-01"),
        _thing("plant-01", "concentrator", snap["plant-01"], model=p.plant.model, position=pit.plant,
               rated_tph=p.plant.rate_tph, fed_by="stockpile-01", cu_grade=p.ore.cu_grade,
               recovery=p.ore.recovery, concentrate_grade=p.ore.concentrate_grade,
               cu_price_usd_t=p.ore.cu_price_usd_t),
        _thing(WHATIF, "scenarios", {k: {"value": v} for k, v in WHATIF_DEFAULTS.items()},
               description="What-if scenarios: set a feature to change an operating condition, "
                           "set it back to the default to undo"),
    ])
    for road, points in pit.roads.items():
        ends = {"road-ramp": ["face-upper", "face-lower", "pit-exit"],
                "road-crusher": ["pit-exit", "weighbridge-01", "crusher-01"],
                "road-dump": ["pit-exit", "dump-01"], "road-dump-2": ["pit-exit", "dump-02"],
                "road-service": ["road-crusher", "fuel-01", "parking", "workshop-01"]}[road]
        things.update([_thing(road, "road", snap[road], connects=ends, length_m=round(_length(points)),
                              grade_max=p.site.ramp_grade if road == "road-ramp" else 0.0)])
    for face in pit.faces.values():
        things.update([_thing(face.name, "face", snap[face.name], material=face.material.name,
                              shovel=face.shovel, destination=face.destination, position=face.position,
                              bench_z=face.position[2])])
        things.update([_thing(face.shovel, "shovel", snap[face.shovel], model=p.shovel.model, face=face.name,
                              material=face.material.name, bucket_m3=p.shovel.bucket_m3, position=face.position)])
    for truck in model.trucks:
        things.update([_thing(truck.name, "haul-truck", snap[truck.name], model=p.truck.model,
                              capacity_t=p.truck.payload_t)])
    return things


def snapshot(model, modes: dict, shift: int) -> dict[str, dict]:
    """Every twin's full feature set at the current simulated time."""
    now = model.env.now
    m = model.metrics
    blast = model.blast
    drone = model.drone_xy()
    hours = max(now / 3600, 1e-9)
    out = {
        "pit-01": {
            "sim_time_s": {"value": round(now, 1), "unit": "s"},
            "shift": {"value": shift},
            "dispatch_mode": {"value": modes["dispatch_mode"]},
            "oversize_mode": {"value": modes["oversize_mode"]},
            "blast_mode": {"value": modes["blast_mode"]},
            "total_t": {"value": round(m.total_t, 1), "unit": "t"},
            "ore_t": {"value": round(m.ore_t, 1), "unit": "t"},
            "waste_t": {"value": round(m.waste_t, 1), "unit": "t"},
            "weighed_t": {"value": round(m.weighed_t, 1), "unit": "t"},
            "weighs": {"value": m.weighs},
            "rate_tph": {"value": round(m.total_t / hours, 1), "unit": "t/h"},
            "fuel_l": {"value": round(m.fuel_l, 1), "unit": "L"},
            "l_per_t_ore": {"value": round(m.fuel_by_material_l["ore"] / m.ore_t, 4) if m.ore_t else 0, "unit": "L/t"},
            "l_per_t_waste": {"value": round(m.fuel_by_material_l["waste"] / m.waste_t, 4) if m.waste_t else 0, "unit": "L/t"},
            "queue_h": {"value": round(m.truck_queue_s / 3600, 2), "unit": "h"},
            "dispatch_fallbacks": {"value": getattr(model.dispatcher, "fallbacks", 0)},
            "blast_phase": {"value": blast.phase},
            "zone_lower": {"value": blast.zones.get("face-lower", "open")},
            "zone_upper": {"value": blast.zones.get("face-upper", "open")},
            "wind_ms": {"value": round(blast.wind_ms, 1) if blast.wind_ms else 0.0, "unit": "m/s"},
            "ore_face_shut_min": {"value": round(m.face_closed_s.get("face-lower", 0) / 60, 1), "unit": "min"},
            "gas_exposure_min": {"value": round(m.gas_exposure_min, 1), "unit": "min"},
            "plant_t": {"value": round(m.plant_t, 1), "unit": "t"},
            "plant_idle_min": {"value": round((m.plant_starved_s + m.plant_stopped_s) / 60, 1), "unit": "min"},
            "pit_stage": {"value": model.stage},
            "pit_depth_m": {"value": -model.pit.lower_z, "unit": "m"},
            "cu_ore_t": {"value": round(m.cu_ore_t, 2), "unit": "t"},
            "cu_value_usd": {"value": round(m.cu_ore_t * model.p.ore.cu_price_usd_t), "unit": "USD"},
            "evacuated": {"value": m.evacuated},
            "breakdowns": {"value": m.breakdowns},
            "headway_min": {"value": round(m.truck_headway_s / 60, 1), "unit": "min"},
        },
        "stockpile-01": {
            "level_t": {"value": round(model.plant.stockpile_t, 1), "unit": "t"},
            "fill": {"value": round(model.plant.stockpile_t / model.plant.capacity_t, 3)},
        },
        "plant-01": {
            "state": {"value": model.plant.state},
            "tonnes": {"value": round(model.plant.tonnes, 1), "unit": "t"},
            "rate_tph": {"value": round(model.plant.tonnes / hours, 1), "unit": "t/h"},
            "starved_min": {"value": round(m.plant_starved_s / 60, 1), "unit": "min"},
            "stopped_min": {"value": round(m.plant_stopped_s / 60, 1), "unit": "min"},
            "cu_t": {"value": round(model.plant.cu_t, 2), "unit": "t"},
            "concentrate_t": {"value": round(model.plant.concentrate_t, 1), "unit": "t"},
        },
        "crusher-01": {
            "state": {"value": model.crusher.state},
            "pocket_t": {"value": round(model.crusher.pocket.level, 1), "unit": "t"},
            "tonnes": {"value": round(model.crusher.tonnes, 1), "unit": "t"},
            "down_min": {"value": round(model.crusher.down_s / 60, 1), "unit": "min"},
            "queue": {"value": len(model.crusher.dump.queue)},
            "boulders": {"value": m.boulders},
            "jams": {"value": m.jams},
            "rockbreaks": {"value": m.rockbreaks},
            "x50_m": {"value": round(model.x50(), 3), "unit": "m"},
            "blocked_min": {"value": round(m.crusher_blocked_s / 60, 1), "unit": "min"},
        },
        "weighbridge-01": {
            "state": {"value": "busy" if model.weighbridge.count else "idle"},
            "queue": {"value": len(model.weighbridge.queue)},
            "weighs": {"value": m.weighs},
            "weighed_t": {"value": round(m.weighed_t, 1), "unit": "t"},
            "busy_min": {"value": round(m.weigh_s / 60, 1), "unit": "min"},
            "queue_min": {"value": round(m.weigh_queue_s / 60, 1), "unit": "min"},
        },
        "drone-01": {
            "state": {"value": "flying" if drone else "docked"},
            "x": {"value": round(drone[0], 1) if drone else 0.0, "unit": "m"},
            "y": {"value": round(drone[1], 1) if drone else 0.0, "unit": "m"},
            "sweeps": {"value": blast.sweeps},
            **{f"{gas.lower()}_{zone}": {"value": blast.readings.get(f"face-{zone}", {}).get(gas, 0.0),
                                         "unit": "mg/m3"}
               for gas in ("CO", "NO2") for zone in ("lower", "upper")},
        },
        "workshop-01": {
            "trucks_in_repair": {"value": sum(1 for t in model.trucks if t.state == "repair")},
            "queue": {"value": sum(1 for t in model.trucks if t.state == "repair_queue")},
            "queue_min": {"value": round(m.workshop_queue_s / 60, 1), "unit": "min"},
            "field_repairs": {"value": len(model.breakdowns)},
        },
        "fuel-01": {
            "queue": {"value": len(model.fuel_bays.queue)},
            "fueling": {"value": model.fuel_bays.count},
            "refuels": {"value": m.refuels},
            "dispensed_l": {"value": round(m.fuel_dispensed_l), "unit": "L"},
        },
    }
    for unit in (model.dozer, model.grader):
        out[unit.name] = {
            "state": {"value": unit.state},
            "x": {"value": round(unit.xy[0], 1), "unit": "m"},
            "y": {"value": round(unit.xy[1], 1), "unit": "m"},
            "z": {"value": round(unit.xy[2], 1), "unit": "m"},
            "down_min": {"value": round(unit.down_s / 60, 1), "unit": "min"},
        }
    out["grader-01"]["rolling_resistance"] = {"value": model.p.site.rolling_resistance}
    for dest, name in DUMP_TWIN.items():
        out[name] = {
            "tonnes": {"value": round(m.tonnes_by_dump[dest], 1), "unit": "t"},
            "queue": {"value": len(model.dumps[dest].queue)},
            "in_use": {"value": any(model.dest(f) == dest for f in model.pit.faces)},
            "volume_m3": {"value": round(m.tonnes_by_dump[dest] / WASTE.loose_density_t_m3), "unit": "m3"},
        }
    on_road = {road: 0 for road in model.pit.roads}
    for truck in model.trucks:
        for road in _roads_used(model, truck):
            on_road[road] += 1
    for road in model.pit.roads:
        out[road] = {
            "state": {"value": "closed" if road in model.closed_roads else "open"},
            "rolling_resistance": {"value": model.p.site.rolling_resistance},
            "trucks": {"value": on_road[road]},
        }
    for face, f in model.pit.faces.items():
        out[face] = {
            "zone": {"value": blast.zones.get(face, "open")},
            "shovel_state": {"value": model.shovels[face].state},
            "x50_m": {"value": round(model.x50(), 3) if f.material.name == "ore" else 0.0, "unit": "m"},
            "trucks_assigned": {"value": sum(1 for t in model.trucks if t.face == face
                                             and t.state in ("to_face", "queued_at_shovel", "loading"))},
            "mined_t": {"value": round(model.face_mined_t[face], 1), "unit": "t"},
            "blasted_t": {"value": round(model.blasted_t[face], 1), "unit": "t"},
            "advance_m": {"value": round(model.advance_m(face), 1), "unit": "m"},
            "blocked_no_rock": {"value": model.shovels[face].state == "no_rock"},
            "x": {"value": round(f.position[0], 1), "unit": "m"},
            "y": {"value": round(f.position[1], 1), "unit": "m"},
            "z": {"value": round(f.position[2], 1), "unit": "m"},
        }
    for face, shovel in model.shovels.items():
        # While digging, the shovel sits on the advancing face; during blast
        # evacuation it walks to a rim pad (shovel.xy).
        if shovel.state not in EVAC_STATES:
            shovel.xy = model.pit.faces[face].position
        x, y, z = shovel.xy
        out[shovel.name] = {
            "state": {"value": shovel.state},
            "face": {"value": face},
            "queue": {"value": sum(1 for r in shovel.resource.queue if r.priority > 0)},
            "load_end_s": {"value": round(shovel.load_end, 1), "unit": "s"},
            "blocked_until_s": {"value": round(min(shovel.blocked_until, 1e9), 1), "unit": "s"},
            "utilization": {"value": round(shovel.busy_s / max(now, 1), 3)},
            "x": {"value": round(x, 1), "unit": "m"},
            "y": {"value": round(y, 1), "unit": "m"},
            "z": {"value": round(z, 1), "unit": "m"},
        }
    for truck in model.trucks:
        x, y, z = model.truck_xy(truck)
        out[truck.name] = {
            "state": {"value": truck.state},
            "face": {"value": truck.face or ""},
            "location": {"value": truck.location},
            "eta_s": {"value": round(truck.eta, 1), "unit": "s"},
            "payload": {"value": round(truck.payload_t, 1), "unit": "t"},
            "x": {"value": round(x, 1), "unit": "m"},
            "y": {"value": round(y, 1), "unit": "m"},
            "z": {"value": round(z, 1), "unit": "m"},
            "fuel_l": {"value": round(truck.fuel_l, 1), "unit": "L"},
            "tank_pct": {"value": round(100 * truck.tank_l / model.p.truck.fuel_tank_l), "unit": "%"},
            "trips": {"value": truck.trips},
        }
    return out


def modify_features(name: str, features: dict) -> dict:
    """Ditto protocol command replacing all features of one twin."""
    return {
        "topic": f"{NAMESPACE}/{name}/things/twin/commands/modify",
        "path": "/features",
        "value": {k: {"properties": v} for k, v in features.items()},
    }


def modify_geometry(model) -> dict:
    """Ditto protocol command replacing the pit layout after a change of stage."""
    return {
        "topic": f"{NAMESPACE}/pit-01/things/twin/commands/modify",
        "path": "/attributes/geometry",
        "value": model.pit.describe(),
    }


def live_message(name: str, subject: str, value: dict, correlation_id: str) -> dict:
    """Ditto protocol live message to a thing (no response expected)."""
    return {
        "topic": f"{NAMESPACE}/{name}/things/live/messages/{subject}",
        "headers": {"content-type": "application/json", "correlation-id": correlation_id,
                    "response-required": False},
        "path": f"/inbox/messages/{subject}",
        "value": value,
    }
