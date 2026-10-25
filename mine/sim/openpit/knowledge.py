"""What any participant knows about the pit without watching it: road network,
travel times and nominal loading times. Shared by the simulator and by the
dispatcher service, which only sees the mine through Ditto."""

import dataclasses
import math

from .params import Params
from .physics import idle_fuel, travel
from .topology import Pit, Route


class PitKnowledge:
    p: Params
    pit: Pit

    def _init_knowledge(self, params: Params) -> None:
        self.p = params
        self.pit = Pit(params.site)
        self._travel_cache: dict = {}
        self.closed_roads: set[str] = set()

    def travel_cost(self, route: Route, payload_t: float) -> tuple[float, float, list[float]]:
        """(time_s, engine_fuel_l, per-leg times) at nominal speed."""
        key = (route.name, round(payload_t, -1))
        if key not in self._travel_cache:
            legs = travel(route, payload_t, self.p.truck, self.p.site)
            time_s = sum(l.time_s for l in legs)
            engine = sum(l.fuel_l for l in legs) - idle_fuel(time_s, self.p.truck)
            self._travel_cache[key] = (time_s, engine, [l.time_s for l in legs])
        return self._travel_cache[key]

    def set_rolling_resistance(self, value: float) -> None:
        if value != self.p.site.rolling_resistance:
            self.p = dataclasses.replace(self.p, site=dataclasses.replace(self.p.site, rolling_resistance=value))
            self._travel_cache.clear()

    def dest(self, face: str) -> str:
        """Where a load from this face goes now: waste uses the reserve dump
        while the road to the main dump is closed."""
        d = self.pit.faces[face].destination
        return "dump-2" if d == "dump" and "road-dump" in self.closed_roads else d

    def loaded_route(self, face: str) -> Route:
        return self.pit.loaded_route(face, self.dest(face))

    def passes(self, face: str) -> int:
        s = self.p.shovel
        bucket_t = s.bucket_m3 * s.fill_factor * self.pit.faces[face].material.loose_density_t_m3
        return math.ceil(self.p.truck.payload_t / bucket_t)

    def mean_load_s(self, face: str) -> float:
        s = self.p.shovel
        return sum(s.exchange_min) / 2 * 60 + s.first_pass_s + (self.passes(face) - 1) * s.pass_cycle_s

    def empty_route(self, truck, face: str) -> Route:
        if truck.location == face:
            return Route(f"{face}-stay", (self.pit.faces[face].position,) * 2)
        if truck.location in self.pit.faces:
            return self.pit.between(truck.location, face)
        route = self.pit.empty_route(face, truck.location)
        if truck.location == "parking" and getattr(truck, "bay", None):
            return Route(f"{route.name}@{truck.name}", (truck.bay, truck.bay_exit) + route.points[1:])
        return route
