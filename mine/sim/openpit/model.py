"""Discrete-event model of one shift of the open pit (SimPy, seconds).

The model is the physical world only. Decisions are delegated to a dispatcher
object, so the same policy code runs here in batch experiments and in the live
service that takes its state from Ditto."""

import math
import random
from dataclasses import dataclass, field

import simpy

from . import hazards
from .knowledge import PitKnowledge
from .params import Params, at_stage
from .physics import idle_fuel
from .safety import CrusherGuard, drone_reentry, manual_reentry
from .topology import Route

DEFAULT_MODES = {"oversize": "A", "blast": "A"}

WHATIF_DEFAULTS = {
    "shovel_01_down": False,
    "shovel_02_down": False,
    "trucks_in_repair": 0,
    "crusher_stop": False,
    "road_dump_closed": False,
    "rain": False,
    "poor_blast": False,
    "calm_wind": False,
    "blast_now": 0,
    "plant_stop": False,
    "pit_stage": 0,
    "dozer_down": False,
}
SHOVEL_SCENARIO = {"shovel_01_down": "face-upper", "shovel_02_down": "face-lower"}
RAIN_ROLLING_RESISTANCE = 0.04
POOR_BLAST_X50_M = 0.30
CALM_WIND_MS = 1.0


def stream(seed: int, name: str) -> random.Random:
    """Independent named stream; a str seed is hashed with SHA-512, so nearby
    seeds give unrelated sequences."""
    return random.Random(f"{seed}:{name}")


@dataclass
class Metrics:
    ore_t: float = 0.0
    waste_t: float = 0.0
    fuel_l: float = 0.0
    fuel_by_material_l: dict = field(default_factory=lambda: {"ore": 0.0, "waste": 0.0})
    tonnes_by_dump: dict = field(default_factory=lambda: {"dump": 0.0, "dump-2": 0.0})
    trips: int = 0
    truck_queue_s: float = 0.0
    truck_headway_s: float = 0.0
    shovel_busy_s: dict = field(default_factory=dict)
    shovel_down_s: dict = field(default_factory=dict)
    crusher_down_s: float = 0.0
    boulders: int = 0
    jams: int = 0
    rockbreaks: int = 0
    rockbreak_s: float = 0.0
    face_closed_s: dict = field(default_factory=dict)
    gas_exposure_min: float = 0.0
    wind_ms: float = float("nan")
    plant_t: float = 0.0
    plant_starved_s: float = 0.0
    plant_stopped_s: float = 0.0
    crusher_blocked_s: float = 0.0
    truck_lost_s: dict = field(default_factory=lambda: {"handover": 0.0, "break": 0.0, "refuel": 0.0,
                                                        "repair": 0.0, "blast": 0.0})
    evacuated: int = 0
    refuels: int = 0
    fuel_dispensed_l: float = 0.0
    breakdowns: int = 0
    breakdown_delay_s: float = 0.0
    workshop_queue_s: float = 0.0
    no_rock_s: float = 0.0
    dozer_down_s: float = 0.0
    cu_ore_t: float = 0.0
    cu_plant_t: float = 0.0
    concentrate_t: float = 0.0

    @property
    def total_t(self) -> float:
        return self.ore_t + self.waste_t

    @property
    def litres_per_t(self) -> float:
        return self.fuel_l / self.total_t if self.total_t else float("nan")


class Truck:
    def __init__(self, name: str, origin: str):
        self.name = name
        self.location = origin
        self.state = "parked"
        self.face: str | None = None
        self.eta: float = 0.0
        self.payload_t = 0.0
        self.route: Route | None = None
        self.route_t0 = 0.0
        self.route_times: list[float] = []
        self.fuel_l = 0.0
        self.trips = 0
        self.stops: list[tuple[float, str, float]] = []
        self.fails_at = math.inf
        self.bay: tuple | None = None
        self.bay_exit: tuple | None = None
        self.dispatched_at: float | None = None
        self.tank_l = 0.0
        self.meal_at = math.inf
        self.meal_done = False
        self.stop_xy: tuple | None = None


class Shovel:
    def __init__(self, env: simpy.Environment, name: str, face: str, xy: tuple = (0.0, 0.0, 0.0)):
        self.name = name
        self.face = face
        self.xy = xy
        self.resource = simpy.PriorityResource(env, capacity=1)
        self.state = "idle"
        self.loading: Truck | None = None
        self.load_end = 0.0
        self.blocked_until = 0.0
        self.busy_s = 0.0
        self.down_s = 0.0

EVAC_STATES = frozenset({"to_safe", "evacuated", "to_face"})


class Crusher:
    def __init__(self, env: simpy.Environment, pocket_t: float):
        self.dump = simpy.Resource(env, capacity=1)
        self.pocket = simpy.Container(env, capacity=pocket_t, init=0)
        self.running = True
        self.stopped_until = 0.0
        self.state = "idle"
        self.down_s = 0.0
        self.tonnes = 0.0
        self.ore_cu_t = 0.0
        self.jams = 0
        self.blocked = False


class Plant:
    """Crushed-ore stockpile and the concentrator that draws from it."""

    def __init__(self, spec):
        self.capacity_t = spec.stockpile_live_t
        self.stockpile_t = spec.stockpile_init_t
        self.running = True
        self.state = "running"
        self.tonnes = 0.0
        self.cu_t = 0.0
        self.concentrate_t = 0.0


class Support:
    """A dozer or a grader: where it is and what it does."""

    def __init__(self, name: str, xy: tuple):
        self.name = name
        self.state = "working" if name.startswith("dozer") else "parked"
        self.xy = xy
        self.down_s = 0.0
        self.since: float | None = None
        self._downs = 0


class BlastState:
    def __init__(self, faces):
        self.phase = "planned"
        self.t_blast = None
        self.wind_ms = None
        self.readings: dict[str, dict] = {}
        self.zones = {face: "open" for face in faces}
        self.drone_from = None
        self.sweeps = 0


class MineModel(PitKnowledge):
    TICK_S = 10.0

    def __init__(self, params: Params, seed: int, dispatcher, *, modes: dict | None = None,
                 guard=None, reentry=None, shovel_failures: bool = True,
                 oversize: bool = True, blast: bool = True, whatif: dict | None = None,
                 carry: dict | None = None):
        self.stage = int((whatif or {}).get("pit_stage") or 0)
        params = at_stage(params, self.stage)
        self._init_knowledge(params)
        self.seed = seed
        self.dispatcher = dispatcher
        self.modes = {**DEFAULT_MODES, **(modes or {})}
        self.guard = guard or CrusherGuard(params)
        self.reentry = reentry
        self.env = simpy.Environment()
        self.metrics = Metrics()
        self._segments: dict[tuple, list] = {}
        self.breakdowns: list[dict] = []
        self.shovels = {f.name: Shovel(self.env, f.shovel, f.name, f.position)
                        for f in self.pit.faces.values()}
        self.crusher = Crusher(self.env, params.crusher.pocket_t)
        self.plant = Plant(params.plant)
        self.face_mined_t = {face: 0.0 for face in self.pit.faces}
        self.blasted_t = dict(params.faces.blasted_init_t)
        self.advance0 = {face: 0.0 for face in self.pit.faces}
        self.dumps = {d: simpy.PriorityResource(self.env, capacity=params.site.waste_dump_spots)
                      for d in ("dump", "dump-2")}
        self.waste_dump = self.dumps["dump"]
        self.workshop = simpy.Resource(self.env, capacity=params.site.workshop_bays)
        self.fuel_bays = simpy.Resource(self.env, capacity=params.site.fuel_bays)
        self.fuel_queue = 0
        self._closing = {face: self.env.event() for face in self.pit.faces}
        self._rock_wait: dict = {}
        dump_xy = self.pit.points["dump"]
        self.dozer = Support("dozer-01", (dump_xy[0] + 60.0, dump_xy[1] + 40.0, dump_xy[2]))
        ws = self.pit.points["workshop"]
        self.grader = Support("grader-01", (ws[0] + 50.0, ws[1] - 40.0, ws[2]))
        self._dozer_release = None
        self.trucks = [Truck(f"truck-{i + 1:02d}", "parking") for i in range(params.site.trucks)]
        for truck, bay, exit_ in zip(self.trucks, self.pit.bays, self.pit.bay_exits):
            truck.bay, truck.bay_exit = bay, exit_
        self.hooks: list = []
        self.oversize = oversize
        self.ore_x50 = hazards.draw_x50(stream(seed, "fragmentation"), params.fragmentation)
        self.blast = BlastState(self.shovels)
        self.whatif = dict(WHATIF_DEFAULTS)
        self._base_rr = params.site.rolling_resistance
        self._scenario_release: dict = {}
        self._blast_trigger = self.env.event()

        self.env.process(self._crusher_feed())
        self.env.process(self._plant_feed())
        self.env.process(self._dozer_failures())
        self.env.process(self._grader())
        st = params.shift_time
        for shovel in self.shovels.values():
            self.env.process(self.block_shovel(shovel, st.handover_min * 60, "handover"))
            if shovel_failures:
                self.env.process(self._shovel_failures(shovel))
        if blast:
            self.env.process(self._blast())
        for key, value in (whatif or {}).items():
            self.set_whatif(key, value)
        tr = params.truck
        tanks = stream(seed, "fuel-tanks")
        for i, truck in enumerate(self.trucks):
            truck.meal_at = st.meal_fallback_h * 3600
            truck.tank_l = tr.fuel_tank_l * tanks.uniform(*tr.tank_at_start)
            fr = stream(seed, f"{truck.name}-failures")
            down = fr.random() < tr.mttr_h / (tr.mtbf_h + tr.mttr_h)
            if carry is not None:
                down = truck.name in carry.get("broken", ())
            truck.fails_at = 0.0 if down else fr.expovariate(1 / (tr.mtbf_h * 3600))
            start = (st.handover_min + st.release_gap_min * i) * 60
            self.metrics.truck_lost_s["handover"] += start
            self.env.process(self._truck(truck, start_delay=start))
        if carry is not None:
            self._apply_carry(carry)

    # -- shift to shift ------------------------------------------------------
    def carry_out(self) -> dict:
        """What the next shift inherits: stockpile, blasted rock, face
        positions, fuel in the tanks and the trucks that are still broken."""
        broken = [t.name for t in self.trucks
                  if t.state in ("to_workshop", "repair_queue", "repair", "breakdown") or self.env.now >= t.fails_at]
        return {"stockpile_t": self.plant.stockpile_t, "blasted_t": dict(self.blasted_t),
                "advance_m": {f: self.advance_m(f) for f in self.pit.faces},
                "tank_l": {t.name: t.tank_l for t in self.trucks}, "broken": broken}

    def _apply_carry(self, carry: dict) -> None:
        self.plant.stockpile_t = min(self.plant.capacity_t, carry.get("stockpile_t", self.plant.stockpile_t))
        self.blasted_t.update(carry.get("blasted_t", {}))
        self.advance0.update(carry.get("advance_m", {}))
        for face in self.pit.faces:
            self.pit.advance(face, self.advance_m(face))
        for truck in self.trucks:
            truck.tank_l = carry.get("tank_l", {}).get(truck.name, truck.tank_l)

    # -- processes ----------------------------------------------------------
    def _load_time(self, face: str, rng: random.Random) -> float:
        s = self.p.shovel
        t = rng.uniform(*s.exchange_min) * 60 + s.first_pass_s
        for _ in range(self.passes(face) - 1):
            t += max(0.5, rng.gauss(1.0, s.pass_cycle_cv)) * s.pass_cycle_s
        return t

    def _schedule(self, points: list, leg_times: list[float]) -> list[float]:
        """No overtaking: on every road section a truck leaves no sooner than one
        headway after the truck that entered it before. A truck broken down on a
        section costs every passing truck a little time."""
        h, now = self.p.site.headway_s, self.env.now
        t, out = now, []
        for (a, b), leg in zip(zip(points, points[1:]), leg_times):
            key = (round(a[0]), round(a[1]), round(b[0]), round(b[1]))
            ahead = self._segments.setdefault(key, [])
            exit_ = t + leg
            before = [x for e, x in ahead if e <= t]
            if before:
                wait = max(0.0, max(before) + h - exit_)
                self.metrics.truck_headway_s += wait
                exit_ += wait
            for bd in self.breakdowns:
                if bd["until"] > t and _near_segment(bd["xy"], a, b, 30.0):
                    exit_ += self.p.site.breakdown_pass_s
                    self.metrics.breakdown_delay_s += self.p.site.breakdown_pass_s
            ahead[:] = [(e, x) for e, x in ahead if x > now - 600.0] + [(t, exit_)]
            out.append(exit_ - t)
            t = exit_
        return out

    def _drive(self, truck: Truck, route: Route, payload_t: float, rng: random.Random, can_fail: bool = False):
        time_s, engine_fuel, leg_times = self.travel_cost(route, payload_t)
        if time_s <= 0:
            return
        factor = math.exp(rng.gauss(0.0, 0.05))
        points = _dedup(route.points)
        legs = self._schedule(points, [t * factor for t in leg_times])
        total = sum(legs)
        run = total
        if can_fail and truck.fails_at < self.env.now + total:
            run = max(0.0, truck.fails_at - self.env.now)
        truck.route, truck.route_t0, truck.route_times = route, self.env.now, legs
        self._burn(truck, engine_fuel * run / total + idle_fuel(run, self.p.truck))
        yield self.env.timeout(run)
        truck.route = None
        if run < total:
            yield from self._breakdown(truck, route, points, legs, run, payload_t, rng)

    def _breakdown(self, truck: Truck, route: Route, points, legs, run: float, payload_t: float, rng):
        """The truck fails on the road: it stops at the shoulder, the field
        crew fixes it there, then it drives on."""
        env, m = self.env, self.metrics
        i, elapsed = 0, run
        while i < len(legs) - 1 and elapsed > legs[i]:
            elapsed -= legs[i]
            i += 1
        f = min(1.0, elapsed / legs[i]) if legs[i] else 1.0
        a, b = points[i], points[i + 1]
        xy = tuple(a[k] + (b[k] - a[k]) * f for k in range(3))
        before, truck.state, truck.stop_xy = truck.state, "breakdown", xy
        repair_s = rng.expovariate(1 / (self.p.truck.field_repair_h * 3600))
        bd = {"xy": xy, "until": env.now + repair_s, "truck": truck.name}
        self.breakdowns.append(bd)
        m.breakdowns += 1
        m.truck_lost_s["repair"] += max(0.0, min(repair_s, self.p.site.shift_h * 3600 - env.now))
        yield env.timeout(repair_s)
        self.breakdowns.remove(bd)
        truck.stop_xy, truck.state = None, before
        tr = self.p.truck
        truck.fails_at = env.now + stream(self.seed, f"{truck.name}-next-{truck.trips}").expovariate(1 / (tr.mtbf_h * 3600))
        rest = Route(f"{route.name}~{truck.name}~{round(env.now)}", (xy,) + tuple(points[i + 1:]))
        yield from self._drive(truck, rest, payload_t, rng, can_fail=True)

    def _burn(self, truck: Truck, litres: float) -> None:
        truck.fuel_l += litres
        truck.tank_l = max(0.0, truck.tank_l - litres)
        self.metrics.fuel_l += litres

    def _idle(self, truck: Truck, seconds: float):
        self._burn(truck, idle_fuel(seconds, self.p.truck))

    def _to_bay(self, truck: Truck, route: Route, rng) -> object:
        """Drive into this truck's own bay at the park-up."""
        return self._drive(truck, Route(f"{route.name}@{truck.name}", route.points[:-1] + (truck.bay_exit, truck.bay)),
                           0.0, rng)

    def _surface_route(self, truck: Truck, dest: str) -> Route:
        if truck.location in self.pit.faces:
            return self.pit.path(truck.location, dest)
        return self.pit.transfer(truck.location, dest)

    def _to_parking_route(self, truck: Truck) -> Route:
        return self._surface_route(truck, "parking")

    def _evacuate(self, truck: Truck, face: str, rng):
        """The face is closed for the blast: the truck leaves the danger zone
        empty and waits at the park-up."""
        self.metrics.evacuated += 1
        truck.state = "to_parking"
        t0 = self.env.now
        yield from self._to_bay(truck, self.pit.path(face, "parking"), rng)
        truck.location = "parking"
        self.metrics.truck_lost_s["blast"] += self.env.now - t0

    def _truck(self, truck: Truck, start_delay: float):
        env, rng = self.env, stream(self.seed, truck.name)
        truck.state = "pre_start"
        yield env.timeout(start_delay)
        while True:
            truck.state = "awaiting_dispatch"
            face_name = yield from self._dispatch(truck)
            if self.blast.zones[face_name] == "closed":
                # Nobody drives into a zone closed for the blast.
                if truck.location != "parking":
                    truck.state = "to_parking"
                    yield from self._to_bay(truck, self._to_parking_route(truck), rng)
                    truck.location = "parking"
                truck.state = "parked"
                yield env.timeout(60.0)
                self.metrics.truck_lost_s["blast"] += 60.0
                continue
            face = self.pit.faces[face_name]
            shovel = self.shovels[face_name]
            truck.face = face_name
            fuel_at_start = truck.fuel_l
            route = self.empty_route(truck, face_name)
            truck.state = "to_face"
            truck.eta = env.now + self.travel_cost(route, 0.0)[0]
            yield from self._drive(truck, route, 0.0, rng, can_fail=True)
            truck.location = face_name

            truck.state = "queued_at_shovel"
            arrived = env.now
            evacuate = no_rock = False
            with shovel.resource.request(priority=1) as req:
                got = yield req | self._closing[face_name]
                waited = env.now - arrived
                self.metrics.truck_queue_s += waited
                self._idle(truck, waited)
                if req not in got or self.blast.zones[face_name] == "closed":
                    evacuate = True
                elif self.blasted_t[face_name] < self.p.truck.payload_t * 0.5:
                    no_rock = True
                else:
                    truck.state = "loading"
                    shovel.state, shovel.loading = "loading", truck
                    load_s = self._load_time(face_name, rng)
                    shovel.load_end = env.now + load_s
                    yield env.timeout(load_s)
                    self._idle(truck, load_s)
                    shovel.busy_s += load_s
                    shovel.state, shovel.loading = "idle", None
            if evacuate:
                yield from self._evacuate(truck, face_name, rng)
                continue
            if no_rock:
                if face_name not in self._rock_wait:
                    self._rock_wait[face_name] = env.event()
                    env.process(self.block_shovel(shovel, None, "no_rock", self._rock_wait[face_name]))
                continue
            truck.payload_t = self.p.truck.payload_t * max(0.9, rng.gauss(1.0, self.p.truck.payload_cv))
            self._dig(face_name, truck.payload_t)

            truck.state = "hauling"
            dest = self.dest(face_name)
            yield from self._drive(truck, self.loaded_route(face_name), truck.payload_t, rng, can_fail=True)
            truck.location = dest

            truck.state = "queued_at_dump"
            arrived = env.now
            if dest == "crusher":
                with self.crusher.dump.request() as req:
                    yield req
                    yield self.crusher.pocket.put(truck.payload_t)
                    waited = env.now - arrived
                    self.metrics.truck_queue_s += waited
                    self._idle(truck, waited)
                    truck.state = "dumping"
                    dump_s = rng.uniform(*self.p.crusher.dump_min) * 60
                    if self.oversize:
                        dump_s += yield from self._oversize(truck)
                    yield env.timeout(dump_s)
                    self._idle(truck, dump_s)
                self.metrics.ore_t += truck.payload_t
                ore = self.p.ore
                self.metrics.cu_ore_t += truck.payload_t * ore.cu_grade * ore.recovery
            else:
                with self.dumps[dest].request(priority=1) as req:
                    yield req
                    waited = env.now - arrived
                    self.metrics.truck_queue_s += waited
                    self._idle(truck, waited)
                    truck.state = "dumping"
                    dump_s = rng.uniform(*self.p.site.waste_dump_min) * 60
                    if dest == "dump" and self.dozer.state == "down":
                        dump_s *= self.p.support.dump_slowdown_without_dozer
                    yield env.timeout(dump_s)
                    self._idle(truck, dump_s)
                self.metrics.waste_t += truck.payload_t
                self.metrics.tonnes_by_dump[dest] += truck.payload_t
            self.metrics.fuel_by_material_l[face.material.name] += truck.fuel_l - fuel_at_start
            truck.payload_t = 0.0
            truck.trips += 1
            self.metrics.trips += 1
            for hook in self.hooks:
                hook("dump", truck, face_name)

    def _dispatch(self, truck: Truck):
        """A dispatcher either answers at once (assign) or later through an
        event (request), as the live dispatcher service does over Ditto."""
        while True:
            yield from self._time_losses(truck)
            if self._in_repair(truck):
                if truck.location != "workshop":
                    truck.state = "to_workshop"
                    yield from self._drive(truck, self._surface_route(truck, "workshop"), 0.0,
                                           stream(self.seed, f"{truck.name}-workshop"))
                    truck.location = "workshop"
                truck.state = "repair"
                yield self.env.timeout(60.0)
            elif all(math.isinf(s.blocked_until) for s in self.shovels.values()):
                if truck.location != "parking":
                    truck.state = "to_parking"
                    yield from self._to_bay(truck, self._to_parking_route(truck),
                                            stream(self.seed, f"{truck.name}-park-{truck.trips}"))
                    truck.location = "parking"
                truck.state = "parked"
                yield self.env.timeout(60.0)
                if self.blast.phase in ("evacuation", "gas"):
                    self.metrics.truck_lost_s["blast"] += 60.0
            else:
                break
        truck.state = "awaiting_dispatch"
        if hasattr(self.dispatcher, "request"):
            face = yield self.dispatcher.request(truck, self)
            return face
        return self.dispatcher.assign(truck, self)

    # -- what-if scenarios ----------------------------------------------------
    def set_whatif(self, key: str, value) -> None:
        """Change an operating condition now; the same call with the default
        value undoes it."""
        if key not in WHATIF_DEFAULTS or self.whatif.get(key) == value:
            return
        self.whatif[key] = value
        if key in SHOVEL_SCENARIO:
            face = SHOVEL_SCENARIO[key]
            if value:
                release = self._scenario_release[face] = self.env.event()
                self.env.process(self.block_shovel(self.shovels[face], None, "stopped", release))
            else:
                release = self._scenario_release.pop(face, None)
                if release is not None and not release.triggered:
                    release.succeed()
        elif key == "crusher_stop":
            self.crusher.running = not value
        elif key == "plant_stop":
            self.plant.running = not value
        elif key == "road_dump_closed":
            (self.closed_roads.add if value else self.closed_roads.discard)("road-dump")
        elif key == "rain":
            self.set_rolling_resistance(RAIN_ROLLING_RESISTANCE if value else self._base_rr)
        elif key == "dozer_down":
            if value:
                self._dozer_release = self.env.event()
                self.env.process(self._dozer_down(None, self._dozer_release))
            elif self._dozer_release is not None and not self._dozer_release.triggered:
                self._dozer_release.succeed()
        elif key == "blast_now":
            if value and self.blast.phase == "planned" and not self._blast_trigger.triggered:
                self._blast_trigger.succeed()

    def _longest_cycle(self) -> float:
        key = self.p.site.rolling_resistance
        if getattr(self, "_longest", (None,))[0] != key:
            self._longest = (key, max(2 * self.travel_cost(self.loaded_route(f), self.p.truck.payload_t)[0]
                                      + self.mean_load_s(f) for f in self.pit.faces) + 120.0)
        return self._longest[1]

    def _time_losses(self, truck: Truck):
        """Planned stops and failures, taken between trips: end-of-shift
        handover, meal, refuelling and unplanned repairs."""
        env, m = self.env, self.metrics
        end = self.p.site.shift_h * 3600
        longest = self._longest_cycle()
        cycle = max(longest, env.now - truck.dispatched_at if truck.dispatched_at is not None else 0.0)
        # No trip is started that would not be back before the park-up.
        if env.now + cycle >= end - self.p.shift_time.stop_early_min * 60:
            truck.state = "handover"
            m.truck_lost_s["handover"] += max(0.0, end - env.now)
            if truck.location != "parking":
                yield from self._to_bay(truck, self._to_parking_route(truck),
                                        stream(self.seed, f"{truck.name}-park"))
                truck.location = "parking"
            yield env.timeout(max(1.0, end - env.now) + 3600.0)
            return
        if not truck.meal_done and env.now >= truck.meal_at:
            t0 = env.now
            if truck.location != "parking":
                truck.state = "to_parking"
                yield from self._to_bay(truck, self._to_parking_route(truck),
                                        stream(self.seed, f"{truck.name}-meal"))
                truck.location = "parking"
            truck.state = "break"
            yield env.timeout(self.p.shift_time.meal_min * 60)
            truck.meal_done = True
            m.truck_lost_s["break"] += env.now - t0
        tr = self.p.truck
        if truck.tank_l < tr.refuel_below * tr.fuel_tank_l:
            t0 = env.now
            truck.state = "to_fuel"
            yield from self._drive(truck, self._surface_route(truck, "fuel"), 0.0,
                                   stream(self.seed, f"{truck.name}-fuel-{truck.trips}"))
            truck.location = "fuel"
            truck.state = "refuel"
            self.fuel_queue += 1
            with self.fuel_bays.request() as req:
                yield req
                self.fuel_queue -= 1
                yield env.timeout(self.p.shift_time.fueling_min * 60)
            m.refuels += 1
            m.fuel_dispensed_l += tr.fuel_tank_l - truck.tank_l
            truck.tank_l = tr.fuel_tank_l
            m.truck_lost_s["refuel"] += env.now - t0
        if env.now >= truck.fails_at:
            rng = stream(self.seed, f"{truck.name}-failures-{truck.trips}")
            t0 = env.now
            if truck.location != "workshop":
                truck.state = "to_workshop"
                yield from self._drive(truck, self._surface_route(truck, "workshop"), 0.0, rng)
                truck.location = "workshop"
            truck.state = "repair_queue"
            queued = env.now
            with self.workshop.request() as req:
                yield req
                m.workshop_queue_s += env.now - queued
                truck.state = "repair"
                repair_s = rng.expovariate(1 / (self.p.truck.mttr_h * 3600))
                yield env.timeout(repair_s)
            m.truck_lost_s["repair"] += min(env.now, end) - min(t0, end)
            truck.fails_at = env.now + rng.expovariate(1 / (self.p.truck.mtbf_h * 3600))
            # The driver has had the meal while the truck was in the workshop.
            truck.meal_done = truck.meal_done or env.now >= truck.meal_at
        truck.dispatched_at = env.now

    def _dig(self, face: str, tonnes: float) -> None:
        """Take a load out of the blasted rock; the face moves forward."""
        self.face_mined_t[face] += tonnes
        self.blasted_t[face] = max(0.0, self.blasted_t[face] - tonnes)
        self.pit.advance(face, self.advance_m(face))

    def advance_m(self, face: str) -> float:
        f = self.p.faces
        density = self.pit.faces[face].material.insitu_density_t_m3
        return self.advance0[face] + self.face_mined_t[face] / (density * f.bench_h_m * f.cut_width_m)

    def _in_repair(self, truck: Truck) -> bool:
        n = int(self.whatif["trucks_in_repair"] or 0)
        return n > 0 and self.trucks.index(truck) >= len(self.trucks) - n

    def x50(self) -> float:
        return POOR_BLAST_X50_M if self.whatif["poor_blast"] else self.ore_x50

    def _ask(self, policy, message: dict):
        if hasattr(policy, "request"):
            answer = yield policy.request(message, self)
            return answer
        return policy.decide(message)
        yield

    # -- module 2: oversize at the crusher ---------------------------------
    def _oversize(self, truck: Truck):
        """Returns extra seconds the truck spends at the dump pocket."""
        f, rng = self.p.fragmentation, stream(self.seed, f"oversize-{truck.name}-{truck.trips}")
        n = hazards.boulders_in_load(rng, truck.payload_t, self.x50(), f, self.p.crusher.feed_opening_mm)
        extra = 0.0
        for _ in range(n):
            self.metrics.boulders += 1
            size_m = self.guard_threshold() * (1 + rng.expovariate(1 / 0.15))
            seen, bridges = rng.random() < f.camera_recall, rng.random() < f.bridge_probability
            detected = seen and self.modes["oversize"] == "B"
            clear_s = rng.expovariate(1 / (f.jam_clear_min * 60))
            if detected:
                action = yield from self._ask(self.guard, {
                    "truck": truck.name, "size_m": round(size_m, 2), "t": self.env.now})
                if action == "rockbreak":
                    self.metrics.rockbreaks += 1
                    extra += f.rockbreak_min * 60
                    continue
            if bridges:
                self.metrics.jams += 1
                self.crusher.jams += 1
                self.crusher.stopped_until = max(self.crusher.stopped_until, self.env.now) + clear_s
        self.metrics.rockbreak_s += extra
        return extra

    def guard_threshold(self) -> float:
        return self.p.fragmentation.oversize_ratio * self.p.crusher.feed_opening_mm / 1000.0

    # -- module 3: blast and re-entry ----------------------------------------
    def _blast(self):
        env, b, st = self.env, self.p.blasting, self.blast
        rng = stream(self.seed, "blast")
        yield env.timeout(b.blast_hour * 3600 - b.evacuation_min * 60) | self._blast_trigger
        st.phase = "evacuation"
        releases = {face: env.event() for face in self.shovels}
        closed_at = {}
        for face, shovel in self.shovels.items():
            st.zones[face] = "closed"
            closed_at[face] = env.now
            # Leave the blast zone: walk to a rim pad, wait until re-entry, walk back.
            env.process(self._evacuate_shovel(shovel, releases[face]))
            self._closing[face].succeed()
        # The fleet stands anyway: the meal is taken now, at the park-up.
        for truck in self.trucks:
            truck.meal_at = min(truck.meal_at, env.now)
        yield env.timeout(b.evacuation_min * 60)

        st.phase, st.t_blast = "gas", env.now
        for face, tonnes in self.p.faces.blast_block_t:
            self.blasted_t[face] += tonnes
        for event in self._rock_wait.values():
            event.succeed()
        self._rock_wait.clear()
        st.wind_ms = hazards.draw_wind(rng, b)
        if self.whatif["calm_wind"]:
            st.wind_ms = CALM_WIND_MS
        self.metrics.wind_ms = st.wind_ms
        tau = hazards.time_constant_s(st.wind_ms, b)
        peaks = hazards.draw_peaks(rng, b)
        self.ore_x50 = hazards.draw_x50(rng, self.p.fragmentation)
        zone_factor = dict(b.zone_factor)
        noise = stream(self.seed, "drone")

        def truth(face, t):
            return {gas: hazards.concentration(pk, zone_factor[face], t, tau) for gas, pk in peaks.items()}

        drone = self.modes["blast"] == "B"
        policy = self.reentry or (drone_reentry if drone else manual_reentry)(self.p)

        def readings(face, t):
            coverage = 1.0 if drone else noise.uniform(*b.manual_coverage)
            return {gas: round(c * coverage * noise.lognormvariate(0.0, 0.1), 3)
                    for gas, c in truth(face, t).items()}

        if drone:
            step = b.drone_cells * b.drone_cell_s
            first = b.drone_start_min * 60 + step
            st.drone_from = env.now + b.drone_start_min * 60
        else:
            step, first = b.manual_recheck_min * 60, b.fixed_reentry_min * 60
        pdk = dict(b.pdk_mg_m3)
        survey_at = st.t_blast + first
        while any(z == "closed" for z in st.zones.values()):
            yield env.timeout(max(0.0, survey_at - env.now))
            survey_at += step
            t = env.now - st.t_blast
            st.sweeps += 1
            for face in self.shovels:
                if st.zones[face] != "closed":
                    continue
                report = {"zone": face, "t_since_blast_s": round(t, 1), "source": "drone" if drone else "manual",
                          "readings": readings(face, t)}
                st.readings[face] = report["readings"]
                permit = yield from self._ask(policy, report)
                if permit:
                    st.zones[face] = "open"
                    self._closing[face] = env.event()
                    self.metrics.face_closed_s[face] = env.now - closed_at[face]
                    actual = truth(face, t)
                    if any(actual[g] >= pdk[g] for g in actual):
                        safe = hazards.safe_time_s(peaks, zone_factor[face], tau, b)
                        self.metrics.gas_exposure_min += (safe - t) / 60
                    releases[face].succeed()
        st.phase = "clear"
        st.drone_from = None

    def drone_xy(self) -> tuple[float, float] | None:
        """Lawnmower path over the pit, one pass of the cell grid per sweep."""
        st, b = self.blast, self.p.blasting
        if st.drone_from is None or self.env.now < st.drone_from:
            return None
        sweep = b.drone_cells * b.drone_cell_s
        f = ((self.env.now - st.drone_from) % sweep) / sweep
        rows = 4
        row = min(rows - 1, int(f * rows))
        along = f * rows - row
        if row % 2:
            along = 1 - along
        g = self.pit.describe()["pit"]
        a, c = g["a_m"] * 0.8, g["b_m"] * 0.8
        y = -c + (row + 0.5) * 2 * c / rows
        half = a * math.sqrt(max(0.0, 1 - (y / c) ** 2))
        return -half + 2 * half * along, y

    def truck_xy(self, truck: Truck) -> tuple[float, float, float]:
        if truck.stop_xy is not None:
            return truck.stop_xy
        if truck.route is None:
            if truck.location == "parking":
                return truck.bay
            if truck.location in self.pit.faces:
                return self.pit.faces[truck.location].position
            return self.pit.points[truck.location]
        elapsed = self.env.now - truck.route_t0
        points = _dedup(truck.route.points)
        for (a, b), leg_s in zip(zip(points, points[1:]), truck.route_times):
            if elapsed <= leg_s:
                f = elapsed / leg_s if leg_s else 1.0
                return tuple(a[k] + (b[k] - a[k]) * f for k in range(3))
            elapsed -= leg_s
        return truck.route.points[-1]

    def _crusher_feed(self):
        env, c = self.env, self.crusher
        per_tick = self.p.crusher.rate_tph * self.TICK_S / 3600.0
        while True:
            yield env.timeout(self.TICK_S)
            if not c.running or env.now < c.stopped_until:
                c.down_s += self.TICK_S
                c.state = "jammed" if env.now < c.stopped_until else "stopped"
                continue
            room = self.plant.capacity_t - self.plant.stockpile_t
            c.blocked = room < per_tick
            if c.blocked:
                self.metrics.crusher_blocked_s += self.TICK_S
                c.state = "blocked"
                continue
            amount = min(per_tick, c.pocket.level)
            if amount > 0:
                yield c.pocket.get(amount)
                c.tonnes += amount
                self.plant.stockpile_t += amount
            c.state = "crushing" if amount > 0 else "idle"

    def _plant_feed(self):
        env, pl, m = self.env, self.plant, self.metrics
        per_tick = self.p.plant.rate_tph * self.TICK_S / 3600.0
        while True:
            yield env.timeout(self.TICK_S)
            if not pl.running:
                m.plant_stopped_s += self.TICK_S
                pl.state = "stopped"
                continue
            amount = min(per_tick, pl.stockpile_t)
            pl.stockpile_t -= amount
            pl.tonnes += amount
            m.plant_t += amount
            ore = self.p.ore
            cu = amount * ore.cu_grade * ore.recovery
            pl.cu_t += cu
            pl.concentrate_t += cu / ore.concentrate_grade
            m.cu_plant_t += cu
            m.concentrate_t += cu / ore.concentrate_grade
            m.plant_starved_s += self.TICK_S * (1 - amount / per_tick)
            pl.state = "running" if amount >= per_tick * 0.999 else "starved"

    def block_shovel(self, shovel: Shovel, seconds: float | None, reason: str, release=None):
        """Take a shovel out of service once its current truck is loaded, for a
        fixed time or until `release` fires."""
        with shovel.resource.request(priority=0) as req:
            yield req
            start = self.env.now
            shovel.state = reason
            shovel.blocked_until = start + seconds if seconds is not None else math.inf
            yield self.env.timeout(seconds) if seconds is not None else release
            shovel.down_s += self.env.now - start
            if reason == "no_rock":
                self.metrics.no_rock_s += self.env.now - start
            shovel.blocked_until = self.env.now
            shovel.state = "idle"

    def _shovel_safe(self, face: str) -> tuple:
        """Rim pad outside the pit exit: shovels wait here while the zone is closed."""
        exit_ = self.pit.ramp[0]
        # Offset so the two shovels do not stack on one spot.
        off = (-90.0, 140.0) if face == "face-upper" else (110.0, 90.0)
        return (exit_[0] + off[0], exit_[1] + off[1], 0.0)

    def _walk_shovel(self, shovel: Shovel, dest: tuple, seconds: float, step_s: float = 30.0):
        """Move shovel.xy from its current point to dest over `seconds`."""
        start = shovel.xy
        t0 = self.env.now
        while True:
            elapsed = self.env.now - t0
            if elapsed >= seconds:
                shovel.xy = dest
                return
            k = elapsed / seconds
            shovel.xy = (start[0] + (dest[0] - start[0]) * k,
                         start[1] + (dest[1] - start[1]) * k,
                         start[2] + (dest[2] - start[2]) * k)
            yield self.env.timeout(min(step_s, seconds - elapsed))

    def _evacuate_shovel(self, shovel: Shovel, release):
        """Walk out of the blast zone, wait for re-entry, walk back to the face."""
        face = shovel.face
        walk_s = 8 * 60  # ~8 min crawl to the rim pad
        with shovel.resource.request(priority=0) as req:
            yield req
            start = self.env.now
            shovel.blocked_until = math.inf
            from_face = self.pit.faces[face].position
            shovel.xy = from_face
            shovel.state = "to_safe"
            yield from self._walk_shovel(shovel, self._shovel_safe(face), walk_s)
            shovel.state = "evacuated"
            yield release
            shovel.state = "to_face"
            back = self.pit.faces[face].position
            yield from self._walk_shovel(shovel, back, walk_s)
            shovel.xy = self.pit.faces[face].position
            shovel.down_s += self.env.now - start
            shovel.blocked_until = self.env.now
            shovel.state = "idle"

    # -- support equipment ----------------------------------------------------
    def _dozer_failures(self):
        rng, s = stream(self.seed, "dozer"), self.p.support
        while True:
            yield self.env.timeout(rng.expovariate(1 / (s.dozer_mtbf_h * 3600)))
            yield from self._dozer_down(rng.expovariate(1 / (s.dozer_mttr_h * 3600)))

    def _dozer_down(self, seconds: float | None, release=None):
        """Without the dozer nobody pushes the tipped rock over the crest:
        the dump takes one truck at a time, further back and slower."""
        dz, dump = self.dozer, self.dumps["dump"]
        dz._downs += 1
        dz.state = "down"
        start = self.env.now
        held = [dump.request(priority=0)
                for _ in range(self.p.site.waste_dump_spots - self.p.support.dump_spots_without_dozer)]
        yield self.env.timeout(seconds) if seconds is not None else release
        for req in held:
            if req.triggered:
                dump.release(req)
            else:
                req.cancel()
        dz._downs -= 1
        dz.down_s += self.env.now - start
        self.metrics.dozer_down_s += self.env.now - start
        if dz._downs == 0:
            dz.state = "working"

    def _grader(self):
        """In rain the grader works the haul roads; rolling resistance comes
        down from 4 % towards 3 % as it covers the network."""
        gr, s = self.grader, self.p.support
        park = gr.xy
        loop = _dedup(self.loaded_route("face-lower").points + tuple(reversed(self.loaded_route("face-lower").points)))
        while True:
            yield self.env.timeout(60.0)
            if not self.whatif["rain"]:
                gr.since, gr.state, gr.xy = None, "parked", park
                continue
            if gr.since is None:
                gr.since = self.env.now
            gr.state = "grading"
            worked = self.env.now - gr.since
            f = min(1.0, worked / (s.grade_network_h * 3600))
            rr = RAIN_ROLLING_RESISTANCE - (RAIN_ROLLING_RESISTANCE - s.graded_rolling_resistance) * f
            self.set_rolling_resistance(round(rr, 3))
            gr.xy = _along(loop, worked * 3.0)

    def _shovel_failures(self, shovel: Shovel):
        rng = stream(self.seed, f"{shovel.name}-failures")
        s = self.p.shovel
        while True:
            yield self.env.timeout(rng.expovariate(1.0 / (s.mtbf_h * 3600)))
            yield from self.block_shovel(shovel, rng.expovariate(1.0 / (s.mttr_min * 60)), "down")

    # -- run ----------------------------------------------------------------
    def run(self, until_s: float | None = None) -> Metrics:
        self.env.run(until=until_s if until_s is not None else self.p.site.shift_h * 3600)
        for shovel in self.shovels.values():
            self.metrics.shovel_busy_s[shovel.name] = shovel.busy_s
            self.metrics.shovel_down_s[shovel.name] = shovel.down_s
        self.metrics.crusher_down_s = self.crusher.down_s
        return self.metrics


def _dedup(points) -> list:
    return [p for i, p in enumerate(points) if i == 0 or p[:2] != points[i - 1][:2]]


def _near_segment(p, a, b, radius: float) -> bool:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    f = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(a[0] + f * dx - p[0], a[1] + f * dy - p[1]) <= radius


def _along(points, distance: float) -> tuple:
    """Point at a distance along a closed loop through the points."""
    legs = [(a, b, math.dist(a[:2], b[:2])) for a, b in zip(points, points[1:])]
    d = distance % max(1.0, sum(n for _, _, n in legs))
    for a, b, n in legs:
        if d <= n:
            f = d / n if n else 0.0
            return tuple(a[k] + (b[k] - a[k]) * f for k in range(3))
        d -= n
    return points[-1]
