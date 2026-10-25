"""Batch A/B experiment: replicate shifts with common random numbers and report
mean and 95 % confidence interval per configuration. Every shift contains one
mass blast at the ore face.

    python -m openpit.experiment --shifts 30
"""

import argparse
import json
import math
import statistics
import sys

from .dispatch import DispatchDispatcher, FixedDispatcher, GreedyDispatcher
from .model import MineModel
from .params import Params

T95 = {5: 2.571, 10: 2.228, 20: 2.086, 30: 2.042, 50: 2.009}
BASE = "A: all modules off"


def t95(n: int) -> float:
    return T95.get(n - 1) or T95[min(T95, key=lambda k: abs(k - (n - 1)))] if n > 1 else float("nan")


def run_shift(params: Params, seed: int, make_dispatcher, modes: dict) -> dict:
    model = MineModel(params, seed, make_dispatcher(params), modes=modes)
    m = model.run()
    hours = params.site.shift_h
    return {
        "total_t": m.total_t,
        "ore_t": m.ore_t,
        "waste_t": m.waste_t,
        "fuel_l": m.fuel_l,
        "l_per_t": m.litres_per_t,
        "l_per_t_ore": m.fuel_by_material_l["ore"] / m.ore_t,
        "l_per_t_waste": m.fuel_by_material_l["waste"] / m.waste_t,
        "fuel_lph_truck": m.fuel_l / hours / params.site.trucks,
        "queue_h": m.truck_queue_s / 3600,
        "shovel_util_upper": m.shovel_busy_s["shovel-01"] / (hours * 3600),
        "shovel_util_lower": m.shovel_busy_s["shovel-02"] / (hours * 3600),
        "crusher_down_min": m.crusher_down_s / 60,
        "jams": m.jams,
        "rockbreaks": m.rockbreaks,
        "closed_ore_min": m.face_closed_s.get("face-lower", 0.0) / 60,
        "closed_waste_min": m.face_closed_s.get("face-upper", 0.0) / 60,
        "gas_exposure_min": m.gas_exposure_min,
        "unsafe_entry": float(m.gas_exposure_min > 0),
        "wind_ms": m.wind_ms,
        "trips": m.trips,
        "cu_ore_t": m.cu_ore_t,
        "cu_plant_t": m.cu_plant_t,
        "evacuated": m.evacuated,
        "refuels": m.refuels,
        "breakdowns": m.breakdowns,
        "workshop_queue_h": m.workshop_queue_s / 3600,
        "headway_min_truck": m.truck_headway_s / 60 / params.site.trucks,
        "no_rock_min": m.no_rock_s / 60,
        "dozer_down_min": m.dozer_down_s / 60,
    }


def summarise(rows: list[dict]) -> dict:
    out = {}
    for key in rows[0]:
        values = [r[key] for r in rows]
        mean = statistics.fmean(values)
        half = t95(len(values)) * statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
        out[key] = (mean, half)
    return out


def paired_gain(a: list[dict], b: list[dict], key: str) -> tuple[float, float]:
    diffs = [rb[key] - ra[key] for ra, rb in zip(a, b)]
    mean = statistics.fmean(diffs)
    half = t95(len(diffs)) * statistics.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) > 1 else 0.0
    return mean, half


def configurations(params: Params, seeds: list[int], reference: bool) -> dict:
    trucks = [f"truck-{i + 1:02d}" for i in range(params.site.trucks)]

    def fixed(n_lower):
        return lambda p: FixedDispatcher.split(trucks, n_lower)

    def dispatch(p):
        return DispatchDispatcher()

    off = {"oversize": "A", "blast": "A"}

    def runs(make, modes):
        return [run_shift(params, s, make, modes) for s in seeds]

    results = {BASE: runs(fixed(params.site.trucks // 2), off)}
    if reference:
        best_k, best_rows = None, None
        for k in range(1, params.site.trucks):
            rows = runs(fixed(k), off)
            if best_rows is None or statistics.fmean(r["total_t"] for r in rows) > \
                    statistics.fmean(r["total_t"] for r in best_rows):
                best_k, best_rows = k, rows
        results[f"ref: best fixed {best_k}/{params.site.trucks - best_k}"] = best_rows
        results["ref: greedy dispatch"] = runs(lambda p: GreedyDispatcher(), off)
    results["M1: dispatch LP"] = runs(dispatch, off)
    results["M2: camera+breaker"] = runs(fixed(params.site.trucks // 2), {"oversize": "B", "blast": "A"})
    results["M3: drone re-entry"] = runs(fixed(params.site.trucks // 2), {"oversize": "A", "blast": "B"})
    results["B: all modules on"] = runs(dispatch, {"oversize": "B", "blast": "B"})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shifts", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--reference", action="store_true", help="also run best fixed split and greedy dispatch")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    params = Params()
    seeds = [args.seed + i for i in range(args.shifts)]
    results = configurations(params, seeds, args.reference)
    summary = {name: summarise(rows) for name, rows in results.items()}
    base = results[BASE]
    gains = {name: {k: paired_gain(base, rows, k) for k in rows[0]} for name, rows in results.items() if rows is not base}
    if args.json:
        json.dump({"shifts": args.shifts, "seeds": seeds, "summary": summary, "gains_vs_A": gains},
                  sys.stdout, indent=2)
        return 0

    cols = [("total_t", "t/shift", 0), ("ore_t", "ore t", 0), ("waste_t", "waste t", 0),
            ("crusher_down_min", "crusher down min", 0), ("closed_ore_min", "ore face shut min", 0),
            ("unsafe_entry", "unsafe entry share", 2),
            ("cu_ore_t", "Cu in ore t", 1), ("l_per_t_ore", "L/t ore", 3), ("l_per_t_waste", "L/t waste", 3), ("queue_h", "queue h", 1)]
    print(f"{args.shifts} shifts x {params.site.shift_h:.0f} h, {params.site.trucks} trucks, "
          f"one blast per shift, seeds {seeds[0]}..{seeds[-1]}")
    print(f"{'config':24s}" + "".join(f"{label:>20s}" for _, label, _ in cols))
    for name, s in summary.items():
        print(f"{name:24s}" + "".join(f"{s[k][0]:>12.{d}f} ±{s[k][1]:<6.{d}f}" for k, _, d in cols))
    print()
    total_a = summary[BASE]["total_t"][0]
    for name, g in gains.items():
        (dt, ht), (do, ho) = g["total_t"], g["ore_t"]
        print(f"{name:24s} vs A: {dt:+7.0f} ± {ht:<5.0f} t/shift ({dt / total_a:+.1%}), ore {do:+6.0f} ± {ho:<5.0f} t, "
              f"Cu {g['cu_ore_t'][0]:+5.1f} t, crusher down {g['crusher_down_min'][0]:+5.0f} min, ore face shut {g['closed_ore_min'][0]:+5.0f} min, "
              f"gas exposure {g['gas_exposure_min'][0]:+5.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
