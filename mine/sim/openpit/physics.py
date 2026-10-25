"""Haul truck travel: speed limited by engine power on grade or by site rules,
fuel from the engine power actually delivered (rimpull method, Cat Performance
Handbook) plus idle burn."""

from dataclasses import dataclass

from .params import G, Site, TruckSpec
from .topology import Route

FLAT = 0.005


@dataclass(frozen=True)
class Leg:
    length_m: float
    grade: float
    speed_kmh: float
    time_s: float
    engine_kw: float
    fuel_l: float


def speed_limit_kmh(grade: float, z: float, site: Site, truck: TruckSpec) -> float:
    if grade < -FLAT:
        return site.speed_limit_downhill_kmh
    if z < 0 and abs(grade) <= FLAT:
        return site.speed_limit_bench_kmh
    return min(site.speed_limit_kmh, truck.top_speed_loaded_kmh)


def leg(length_m: float, grade: float, z: float, mass_t: float, truck: TruckSpec, site: Site) -> Leg:
    resistance = grade + site.rolling_resistance
    limit = speed_limit_kmh(grade, z, site, truck)
    force_n = mass_t * 1000.0 * G * resistance
    wheel_kw_max = truck.gross_power_kw * truck.powertrain_efficiency
    if force_n > 0:
        v_power = wheel_kw_max * 1000.0 / force_n
        v = min(limit / 3.6, v_power)
        engine_kw = force_n * v / 1000.0 / truck.powertrain_efficiency
    else:
        v = limit / 3.6
        engine_kw = 0.0
    time_s = length_m / v
    fuel_l = (truck.idle_fuel_lph + truck.bsfc_l_kwh * engine_kw) * time_s / 3600.0
    return Leg(length_m, grade, v * 3.6, time_s, engine_kw, fuel_l)


def travel(route: Route, payload_t: float, truck: TruckSpec, site: Site) -> list[Leg]:
    mass = truck.empty_mass_t + payload_t
    return [leg(length, grade, start[2], mass, truck, site) for length, grade, start in route.segments()]


def idle_fuel(seconds: float, truck: TruckSpec) -> float:
    return truck.idle_fuel_lph * seconds / 3600.0
