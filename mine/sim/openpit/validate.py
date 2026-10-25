"""Validation against the real world: what the model produces next to published
measurements of the same equipment and of comparable mines.

    python -m openpit.validate --shifts 10
"""

import argparse
import math
import statistics
import sys

from .dispatch import DispatchDispatcher, FixedDispatcher
from .knowledge import PitKnowledge
from .model import MineModel
from .params import Params
from .physics import leg

SHIFTS_PER_YEAR = 730

REFERENCES = {
    "ramp_speed": (12.0, 12.9, "km/h",
                   "Cat 'Large Mining Truck' brochure: 793F loaded on 10 % grade 12.9 km/h; "
                   "SRK 'Electrification of transportation in open pits': ~12 km/h on a 10 % ramp"),
    "passes": (4, 5, "",
               "Hitachi EX5600-6 brochure KS-EN173Q: 29 m3 loading shovel, 4 or 5 passes for a 221 t truck"),
    "load_min": (2.5, 3.1, "min",
                 "Cat field study 793B/793C (Cat engineers, stopwatch): load area 3.08 min; "
                 "Cat Truck Loading System Match: 42 s exchange + 32 s per pass, 4 passes = 2.3 min"),
    "dump_min": (1.0, 2.25, "min",
                 "Cat Performance Handbook ed. 50: maneuver and dump 1.0-1.2 min; "
                 "Cat field study 793B/793C: dump area 2.25 min"),
    "fuel_lph": (121, 240, "L/h",
                 "Cat Performance Handbook, 793F hourly fuel: 121/159/196 L/h (low/medium/high load); "
                 "Cat field study 793B/793C: 36-63 US gal/h = 137-240 L/h"),
    "l_per_t": (0.25, 0.42, "L/t",
                "Large copper mines benchmark (8 mines, J. Min. Metall. A, 2026): 2.39 t per litre = 0.42 L/t "
                "for all mine diesel; haulage is the larger share, so trucks alone sit below it"),
    "effective_h": (6.6, 9.0, "h",
                    "Time studies of 12 h shifts: 6.6 h (55 %) Obi Island; 7.1 h (59 %) JMMCE 2018; "
                    "utilisation 59-75 % (JAMERE); losses: shift change, meal, safety meeting, service, blasting"),
    "t_per_t_capacity": (0, 5000, "t/y per t",
                         "Large copper mines benchmark: annual production up to 5 000 t per tonne of haul capacity"),
    "t_per_m3_bucket": (100_000, 270_000, "t/y per m3",
                        "Large copper mines benchmark: 100 000-270 000 t per year per m3 of loading capacity"),
}


def run(params: Params, seed: int, modules_on: bool):
    trucks = [f"truck-{i + 1:02d}" for i in range(params.site.trucks)]
    dispatcher = DispatchDispatcher() if modules_on else FixedDispatcher.split(trucks, params.site.trucks // 2)
    mode = "B" if modules_on else "A"
    return MineModel(params, seed, dispatcher, modes={"oversize": mode, "blast": mode}).run()


def measure(params: Params, shifts: int, seed: int) -> dict:
    p, k = params, PitKnowledge()
    k._init_knowledge(p)
    gross = p.truck.gross_mass_t
    hours = p.site.shift_h
    ms = [run(p, seed + i, True) for i in range(shifts)]
    mean = lambda f: statistics.fmean(f(m) for m in ms)
    total = mean(lambda m: m.total_t)
    trips = mean(lambda m: m.trips)
    queue_h = mean(lambda m: m.truck_queue_s / 3600)
    route = {f: k.pit.loaded_route(f, k.pit.faces[f].destination) for f in k.pit.faces}
    length = {f: sum(l for l, _, _ in r.segments()) for f, r in route.items()}
    lift = {f: -k.pit.faces[f].position[2] for f in k.pit.faces}
    cycle = p.site.trucks * hours * 60 / trips
    return {
        "ramp_speed": leg(1000.0, p.site.ramp_grade, -50.0, gross, p.truck, p.site).speed_kmh,
        "passes": statistics.fmean(k.passes(f) for f in k.pit.faces),
        "load_min": statistics.fmean(k.mean_load_s(f) for f in k.pit.faces) / 60,
        "dump_min": sum(p.crusher.dump_min) / 2,
        "fuel_lph": mean(lambda m: m.fuel_l) / hours / p.site.trucks,
        "l_per_t": mean(lambda m: m.litres_per_t),
        "effective_h": hours - mean(lambda m: sum(m.truck_lost_s.values())) / p.site.trucks / 3600,
        "t_per_t_capacity": total * SHIFTS_PER_YEAR / (p.site.trucks * p.truck.payload_t),
        "t_per_m3_bucket": total * SHIFTS_PER_YEAR / (len(k.pit.faces) * p.shovel.bucket_m3),
        "_info": {
            "shift_t": total, "trips": trips, "cycle_min": cycle, "queue_share": queue_h / (p.site.trucks * hours),
            "haul_km": {f: round(v / 1000, 2) for f, v in length.items()}, "lift_m": lift,
            "truck_tph": total / (p.site.trucks * hours),
            "shovel_util": {s: round(v / (hours * 3600), 2) for s, v in ms[0].shovel_busy_s.items()},
            "lost_h_per_truck": {k: round(mean(lambda m: m.truck_lost_s[k]) / p.site.trucks / 3600, 2)
                                 for k in ms[0].truck_lost_s},
            "following_min_per_truck": round(mean(lambda m: m.truck_headway_s) / p.site.trucks / 60, 1),
        },
    }


def verdict(value: float, lo: float, hi: float) -> str:
    if lo <= value <= hi:
        return "в диапазоне"
    if lo * 0.98 <= value <= hi * 1.02:
        return "на границе"
    ratio = value / hi if value > hi else value / lo
    return f"{'выше' if value > hi else 'ниже'} ×{ratio:.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shifts", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    got = measure(Params(), args.shifts, args.seed)
    info = got.pop("_info")
    print(f"{args.shifts} shifts, all modules on (mode B); real ranges with sources below\n")
    print(f"{'metric':20s}{'model':>12s}{'real range':>22s}  verdict")
    for key, (lo, hi, unit, _) in REFERENCES.items():
        v = got[key]
        fmt = (lambda x: f"{x:,.0f}") if max(abs(v), hi) >= 100 else (lambda x: f"{x:.2f}")
        print(f"{key:20s}{fmt(v):>12s}{fmt(lo) + ' - ' + fmt(hi) + ' ' + unit:>22s}  {verdict(v, lo, hi)}")
    print("\nmodel context:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in info.items()})
    print("\nsources:")
    for key, (*_, src) in REFERENCES.items():
        print(f"  {key}: {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
