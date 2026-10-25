"""What-if study: each scenario held for the whole shift, all modules off (A)
against all modules on (B), on the same replicated shifts.

    python -m openpit.whatif --shifts 20
"""

import argparse
import json
import statistics
import sys

from .dispatch import DispatchDispatcher, FixedDispatcher
from .experiment import paired_gain, summarise
from .model import MineModel
from .params import Params

SCENARIOS = {
    "baseline": {},
    "shovel-01 (waste) stopped": {"shovel_01_down": True},
    "shovel-02 (ore) stopped": {"shovel_02_down": True},
    "3 trucks in repair": {"trucks_in_repair": 3},
    "crusher planned stop": {"crusher_stop": True},
    "road to dump-01 closed": {"road_dump_closed": True},
    "rain (rolling resistance 4 %)": {"rain": True},
    "dozer down at the waste dump": {"dozer_down": True},
    "poorly blasted block (x50 0.30 m)": {"poor_blast": True},
    "calm at the blast (wind 1 m/s)": {"calm_wind": True},
    "concentrator stopped (mill repair)": {"plant_stop": True},
    "pit +2 years (ore bench -195 m)": {"pit_stage": 1},
    "pit +6 years (ore bench -255 m)": {"pit_stage": 3},
}


def run(params: Params, seed: int, modules_on: bool, whatif: dict) -> dict:
    trucks = [f"truck-{i + 1:02d}" for i in range(params.site.trucks)]
    dispatcher = DispatchDispatcher() if modules_on else FixedDispatcher.split(trucks, params.site.trucks // 2)
    mode = "B" if modules_on else "A"
    m = MineModel(params, seed, dispatcher, modes={"oversize": mode, "blast": mode}, whatif=whatif).run()
    return {
        "total_t": m.total_t, "ore_t": m.ore_t, "waste_t": m.waste_t,
        "l_per_t": m.litres_per_t,
        "crusher_down_min": m.crusher_down_s / 60,
        "closed_ore_min": m.face_closed_s.get("face-lower", 0.0) / 60,
        "gas_exposure_min": m.gas_exposure_min,
        "plant_t": m.plant_t,
        "plant_idle_min": (m.plant_starved_s + m.plant_stopped_s) / 60,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shifts", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    params = Params()
    seeds = [args.seed + i for i in range(args.shifts)]

    table = {}
    for name, whatif in SCENARIOS.items():
        a = [run(params, s, False, whatif) for s in seeds]
        b = [run(params, s, True, whatif) for s in seeds]
        table[name] = {"A": summarise(a), "B": summarise(b), "B_minus_A": paired_gain(a, b, "total_t")}
    if args.json:
        json.dump({"shifts": args.shifts, "seeds": seeds, "scenarios": SCENARIOS, "table": table},
                  sys.stdout, indent=2)
        return 0

    base_a = table["baseline"]["A"]["total_t"][0]
    print(f"{args.shifts} shifts per cell, seeds {seeds[0]}..{seeds[-1]}; A = all modules off, B = all on")
    print(f"{'scenario':36s}{'A t/shift':>11s}{'vs base':>9s}{'B t/shift':>11s}{'B - A':>16s}"
          f"{'L/t A':>8s}{'L/t B':>8s}{'crusher↓ A/B min':>18s}{'ore shut A/B':>14s}{'gas A/B min':>13s}{'plant t A/B':>16s}")
    for name, row in table.items():
        a, b = row["A"], row["B"]
        d, h = row["B_minus_A"]
        print(f"{name:36s}{a['total_t'][0]:>11.0f}{a['total_t'][0] / base_a - 1:>+9.0%}{b['total_t'][0]:>11.0f}"
              f"{d:>+9.0f} ±{h:<5.0f}{a['l_per_t'][0]:>8.3f}{b['l_per_t'][0]:>8.3f}"
              f"{a['crusher_down_min'][0]:>10.0f}/{b['crusher_down_min'][0]:<7.0f}"
              f"{a['closed_ore_min'][0]:>7.0f}/{b['closed_ore_min'][0]:<6.0f}"
              f"{a['gas_exposure_min'][0]:>6.1f}/{b['gas_exposure_min'][0]:<6.1f}"
              f"{a['plant_t'][0]:>8.0f}/{b['plant_t'][0]:<7.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
