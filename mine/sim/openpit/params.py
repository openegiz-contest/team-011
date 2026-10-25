"""Equipment and site constants. Every value carries its source in SOURCES."""

from dataclasses import dataclass, field

G = 9.81
DIESEL_DENSITY_KG_L = 0.835


@dataclass(frozen=True)
class TruckSpec:
    model: str = "Cat 793F"
    payload_t: float = 226.8
    gross_mass_t: float = 386.0
    gross_power_kw: float = 1976.0
    powertrain_efficiency: float = 0.687
    top_speed_loaded_kmh: float = 60.0
    bsfc_kg_kwh: float = 0.205
    idle_fuel_lph: float = 25.0
    payload_cv: float = 0.03
    mtbf_h: float = 30.0
    mttr_h: float = 3.5
    # A failure out on the road is fixed there by the field service crew.
    field_repair_h: float = 1.0
    fuel_tank_l: float = 4354.0
    refuel_below: float = 0.25
    tank_at_start: tuple = (0.30, 1.0)

    @property
    def empty_mass_t(self) -> float:
        return self.gross_mass_t - self.payload_t

    @property
    def bsfc_l_kwh(self) -> float:
        return self.bsfc_kg_kwh / DIESEL_DENSITY_KG_L


@dataclass(frozen=True)
class ShovelSpec:
    model: str = "Hitachi EX5600-6 (loading shovel)"
    bucket_m3: float = 29.0
    fill_factor: float = 0.90
    pass_cycle_s: float = 30.0
    pass_cycle_cv: float = 0.10
    first_pass_s: float = 3.0
    exchange_min: tuple = (0.6, 0.8)
    mtbf_h: float = 8.0
    mttr_min: float = 30.0


@dataclass(frozen=True)
class CrusherSpec:
    model: str = "Metso Superior MKIII 60-89 (primary gyratory)"
    feed_opening_mm: float = 1525.0
    rate_tph: float = 6000.0
    pocket_t: float = 450.0
    dump_min: tuple = (1.0, 1.2)


@dataclass(frozen=True)
class ShiftTime:
    handover_min: float = 30.0
    stop_early_min: float = 20.0
    # Trucks leave the park-up one at a time, nearest the exit first.
    release_gap_min: float = 2.0
    # The meal is taken at the park-up while the faces are closed for the
    # blast; without a blast it falls back to this hour.
    meal_min: float = 40.0
    meal_fallback_h: float = 7.0
    fueling_min: float = 12.0


@dataclass(frozen=True)
class PlantSpec:
    model: str = "Concentrator, SAG and ball mills, 16 Mt/y"
    rate_tph: float = 2000.0
    stockpile_live_t: float = 25000.0
    stockpile_init_t: float = 12000.0


@dataclass(frozen=True)
class Faces:
    bench_h_m: float = 15.0
    cut_width_m: float = 25.0
    blasted_init_t: tuple = (("face-upper", 90000.0), ("face-lower", 45000.0))
    blast_block_t: tuple = (("face-upper", 45000.0), ("face-lower", 25000.0))


@dataclass(frozen=True)
class OreGrade:
    cu_grade: float = 0.0035
    recovery: float = 0.88
    concentrate_grade: float = 0.24
    cu_price_usd_t: float = 9500.0


PIT_STAGES = {0: -165.0, 1: -195.0, 2: -225.0, 3: -255.0}
PIT_STAGE_YEARS = {0: 0, 1: 2, 2: 4, 3: 6}


@dataclass(frozen=True)
class Material:
    name: str
    insitu_density_t_m3: float
    swell_factor: float

    @property
    def loose_density_t_m3(self) -> float:
        return self.insitu_density_t_m3 / self.swell_factor


ORE = Material("ore", 2.70, 1.40)
WASTE = Material("waste", 2.60, 1.40)


@dataclass(frozen=True)
class Site:
    rolling_resistance: float = 0.02
    ramp_grade: float = 0.08
    speed_limit_kmh: float = 50.0
    speed_limit_downhill_kmh: float = 35.0
    speed_limit_bench_kmh: float = 25.0
    # No overtaking on a haul road: the truck behind keeps at least this gap.
    headway_s: float = 10.0
    waste_dump_spots: int = 3
    waste_dump_min: tuple = (1.0, 1.2)
    # Ore trucks stop on a single-lane weighbridge before the dump pocket.
    weighbridge_lanes: int = 1
    weigh_min: tuple = (0.5, 0.8)
    weigh_cv: float = 0.01
    workshop_bays: int = 2
    fuel_bays: int = 2
    # A broken-down truck on the road: the others pass it one lane at a time.
    breakdown_pass_s: float = 60.0
    shift_h: float = 12.0
    trucks: int = 10
    lower_bench_z: float = -165.0


@dataclass(frozen=True)
class Fragmentation:
    x50_m: float = 0.22
    x50_cv: float = 0.10
    uniformity_n: float = 1.4
    oversize_ratio: float = 0.8
    boulder_mean_t: float = 4.4
    bridge_probability: float = 0.3
    jam_clear_min: float = 30.0
    camera_recall: float = 0.90
    rockbreak_min: float = 3.0


@dataclass(frozen=True)
class Blasting:
    blast_hour: float = 6.0
    evacuation_min: float = 15.0
    fixed_reentry_min: float = 120.0
    manual_recheck_min: float = 30.0
    manual_coverage: tuple = (0.6, 1.0)
    legal_min_reentry_min: float = 30.0
    pdk_mg_m3: tuple = (("CO", 20.0), ("NO2", 2.0))
    peak_median_mg_m3: tuple = (("CO", 400.0), ("NO2", 40.0))
    peak_cv: float = 0.35
    tau_ref_min: float = 25.0
    wind_ref_ms: float = 4.0
    wind_weibull_k: float = 2.0
    wind_weibull_scale_ms: float = 4.5
    wind_min_ms: float = 0.5
    zone_factor: tuple = (("face-lower", 1.0), ("face-upper", 0.4))
    drone_cells: int = 24
    drone_cell_s: float = 30.0
    drone_start_min: float = 5.0


@dataclass(frozen=True)
class Support:
    """Dozer on the waste dump and grader on the haul roads."""
    dozer_mtbf_h: float = 40.0
    dozer_mttr_h: float = 1.5
    dump_spots_without_dozer: int = 1
    dump_slowdown_without_dozer: float = 1.5
    graded_rolling_resistance: float = 0.03
    grade_network_h: float = 2.0


@dataclass(frozen=True)
class Params:
    truck: TruckSpec = field(default_factory=TruckSpec)
    shovel: ShovelSpec = field(default_factory=ShovelSpec)
    crusher: CrusherSpec = field(default_factory=CrusherSpec)
    site: Site = field(default_factory=Site)
    fragmentation: Fragmentation = field(default_factory=Fragmentation)
    blasting: Blasting = field(default_factory=Blasting)
    plant: PlantSpec = field(default_factory=PlantSpec)
    shift_time: ShiftTime = field(default_factory=ShiftTime)
    faces: Faces = field(default_factory=Faces)
    ore: OreGrade = field(default_factory=OreGrade)
    support: Support = field(default_factory=Support)


def at_stage(params: Params, stage: int) -> Params:
    """The same mine `stage` steps of deepening later: the ore bench and the
    ramp end are lower, everything else is unchanged."""
    from dataclasses import replace
    z = PIT_STAGES.get(int(stage or 0), PIT_STAGES[0])
    return params if z == params.site.lower_bench_z else replace(params, site=replace(params.site, lower_bench_z=z))


SOURCES = {
    "truck.payload_t, gross_mass_t, gross_power_kw, top_speed_loaded_kmh":
        "Caterpillar 793F Mining Truck Specalog AEHQ8089: nominal payload 226.8 t, "
        "target GMW 386 007 kg, gross power SAE J1995 1976 kW, top speed loaded 60 km/h. "
        "https://s7d2.scene7.com/is/content/Caterpillar/CM20171018-29798-24603",
    "truck.powertrain_efficiency":
        "Calibrated to the published gradeability point: a loaded 793F climbs a 10 % total-resistance "
        "grade at 12.9 km/h (Cat 'Large Mining Truck' brochure CM20201027; SRK 'Electrification of "
        "transportation in open pits': ~12 km/h on a 10 % ramp). 386 t x 9.81 x 0.10 x 3.58 m/s = 1357 kW "
        "at the wheels = 69 % of 1976 kW gross. The Cat Handbook worked example (85 %) gives 16 km/h, "
        "faster than the real truck, because on this grade the truck is held in first gear.",
    "truck.mtbf_h, mttr_h":
        "Assumption: unplanned truck failures every 30 h on average, 3.5 h in the workshop including travel "
        "and waiting, so mechanical availability is ~88 % (89 % for the best unit in a mine time study, "
        "JAMERE; 85-90 % is the usual planning figure for large haul trucks).",
    "truck.bsfc_kg_kwh, idle_fuel_lph":
        "Assumption: typical brake-specific fuel consumption of large 4-stroke mining diesels "
        "(~200-210 g/kWh near rated load) and idle burn. Checked against the Cat Performance "
        "Handbook hourly fuel table for 793F: 32.1 / 41.9 / 51.8 US gal/h (121 / 159 / 196 L/h) "
        "for low / medium / high load factor.",
    "truck.payload_cv":
        "Assumption: payload scatter within the Cat 10/10/20 overload policy.",
    "shovel.bucket_m3, fill_factor":
        "Hitachi EX5600-6 brochure KS-EN173Q: loading shovel 29.0 m3 heaped for 1800 kg/m3; "
        "4-5 passes for a 221 t truck. https://hitachicm.com.au/wp-content/uploads/2021/05/EX5600-6_KS-EN173Q-small.pdf",
    "shovel.pass_cycle_s, first_pass_s, exchange_min":
        "Caterpillar Performance Handbook ed. 50, 'Typical fixed times for hauling units': "
        "truck exchange 0.6-0.8 min, large shovel pass cycle 0.45-0.60 min, first pass 0.05-0.10 min.",
    "shovel.mtbf_h, mttr_min":
        "Assumption: short unplanned shovel stops (face cleanup, cable/hose, operator) "
        "about once per 8 h, 30 min mean duration.",
    "crusher.*":
        "Metso Superior MKIII brochure: 60-89 feed opening 1525 mm, 5337-7625 t/h at 178 mm OSS "
        "(SG 2.75). Rate 6000 t/h chosen inside that range; 450 t dump pocket is an assumption "
        "(about two truckloads). https://www.metso.com/globalassets/portfolio/lokotrack-lt-series/superior-mkiii-primary-gyratory-4435-03-25-web.pdf",
    "crusher.dump_min, site.waste_dump_min":
        "Caterpillar Performance Handbook ed. 50: maneuver and dump time at dump point 1.0-1.2 min.",
    "site.weighbridge_lanes, weigh_min, weigh_cv":
        "Assumption: one in-motion / static truck scale on the crusher approach; spotting and weigh "
        "0.5-0.8 min (typical open-pit weighbridge cycle for ultra-class trucks); scale reading CV 1 % "
        "on top of shovel fill. Ore tonnes for the shift are booked from this reading; waste skips the scale.",
    "ORE, WASTE":
        "Assumption: hard-rock copper porphyry densities 2.6-2.7 t/m3, swell factor 1.40.",
    "site.rolling_resistance":
        "Caterpillar Performance Handbook ed. 50: 2 % rolling resistance for a maintained haul road "
        "(worked examples in the 'Mining & Off-Highway Trucks' section).",
    "site.ramp_grade":
        "Assumption: 8 % ramp, a common design grade for ultra-class haul roads (practical range 8-10 %).",
    "site.speed_limit_*":
        "Assumption: site traffic rules - 50 km/h haul road, 35 km/h downhill on the ramp, 25 km/h on benches.",
    "site.headway_s":
        "Assumption: no overtaking on haul roads, the follower keeps a gap; 10 s is about 50-100 m at 20-35 km/h, "
        "in line with the usual 50 m minimum following distance for haul trucks in site traffic rules.",
    "fragmentation.x50_m, uniformity_n":
        "Kuz-Ram fragmentation model with Rosin-Rammler size distribution, P(>x) = exp(-ln2 (x/x50)^n) "
        "(Cunningham, 1983 and 2005). x50 0.22 m and n 1.4 are typical of hard-rock production blasts; "
        "x50 varies between blasts (cv 10 %).",
    "fragmentation.oversize_ratio, bridge_probability, boulder_mean_t":
        "Assumption: a block above 0.8 of the feed opening (1.22 m for 1525 mm) can bridge the gyratory; "
        "30 % of such blocks do; a 1.2-1.6 m block weighs ~4.4 t (shape factor 0.6, 2.7 t/m3).",
    "fragmentation.jam_clear_min":
        "Assumption: manual clearing of a bridged primary crusher, 30 min mean (exponential).",
    "fragmentation.camera_recall, rockbreak_min":
        "Assumption: dump-pocket camera finds 90 % of oversize blocks; a pedestal rock breaker "
        "reduces a detected block in about 3 min while the crusher waits.",
    "blasting.legal_min_reentry_min":
        "PB 13-407-01 (Unified safety rules for blasting), para 43: after a mass blast people may enter "
        "the pit only after mine rescue reports toxic gases below MAC, and not earlier than 30 min.",
    "blasting.fixed_reentry_min, manual_recheck_min, manual_coverage":
        "Assumption (mode A): the mine rescue crew samples the zone with hand-held gas meters 120 min after "
        "the blast (the ToR value) and every 30 min after that until below MAC; a few hand samples catch "
        "60-100 % of the zone's worst concentration.",
    "blasting.pdk_mg_m3":
        "Russian hygienic standard GN 2.2.5.3532-18, work-zone MAC: CO 20 mg/m3, NO2 2 mg/m3.",
    "blasting.peak_*, tau_*, wind_*, zone_factor":
        "Assumption: post-blast cloud peak at the blasted face CO ~400 mg/m3, NO2 ~40 mg/m3 (lognormal, "
        "cv 35 %); exponential clearing with time constant 25 min at 4 m/s wind, inversely proportional "
        "to wind speed; wind speed Weibull(k=2, scale 4.5 m/s); the upper face sees 40 % of the peak.",
    "blasting.drone_*":
        "Assumption: the drone flies a 24-cell grid over the pit, 30 s per cell (12 min per sweep), "
        "starting 5 min after the blast.",
    "shift_time.release_gap_min":
        "Assumption: after the pre-start check trucks leave the park-up one at a time through a single exit, "
        "2 min apart, so the fleet is out within about 20 min of the end of the handover.",
    "shift_time.*":
        "Time studies of 12 h shifts in open pits: effective work 6.6 h (55 %, Obi Island) and 7.1 h "
        "(59 %, JMMCE 2018, losses: safety meeting 1 h, lunch 1 h, service 1.5 h, leaving the pit early); "
        "utilisation 59-75 % (JAMERE). Used here: 30 min handover at the start, no new trips in the last "
        "20 min, a 40 min meal at the park-up while the faces are closed for the blast (the fleet stands "
        "anyway, so the meal costs no extra haulage), refuelling when the tank is low.",
    "shift_time.fueling_min, site.fuel_bays, truck.fuel_tank_l, refuel_below, tank_at_start":
        "Cat 793F Specalog: fuel tank 4354 L (1150 US gal). A fuel bay with fast-fill nozzles (~400 L/min) fills "
        "3-3.5 kL in about 8-9 min; with positioning 12 min. Assumption: two bays beside the service road; a truck "
        "goes to fuel when the tank falls below 25 %; tanks are 30-100 % full at shift start (trucks fuel every "
        "1-2 shifts at ~150 L/h).",
    "site.workshop_bays":
        "Assumption: two truck repair bays in the workshop, usual for a fleet of about ten ultra-class trucks.",
    "truck.field_repair_h, site.breakdown_pass_s":
        "Assumption: a truck that fails on the road is pushed to the shoulder and fixed by the field service "
        "truck, 1 h mean; while it stands there, passing trucks lose about 1 min each (one lane, spotter).",
    "ore.*":
        "Assumption for a porphyry copper deposit of the Aktogay/Bozshakol type (KAZ Minerals reports: "
        "sulphide grades 0.33-0.36 % Cu, recovery 85-90 %); concentrate 24 % Cu; copper price 9 500 USD/t "
        "(LME 2025 average range 9 000-10 000 USD/t).",
    "support.*":
        "Assumption: the waste dump needs a dozer to push the tipped rock over the crest; without it trucks "
        "tip one at a time and further back (1 spot, +50 % time). Dozer fails every 40 h, 1.5 h repair. "
        "Rain raises rolling resistance to 4 %; the grader brings the network back to 3 % over 2 h "
        "(Cat Handbook: 3 % for a maintained road with some flex, 4 % rutted or wet).",
    "plant.rate_tph":
        "Sized to the pit: 10 trucks and one ore shovel deliver ~1 900 t/h of ore over a shift with its "
        "planned stops (openpit.validate), so the plant takes 2 000 t/h = 16 Mt/y at ~92 % availability. "
        "For scale, KAZ Minerals' Aktogay sulphide plant is 25 Mt/y and Bozshakol 30 Mt/y (company reports); "
        "those pits run far larger fleets.",
    "plant.stockpile_live_t, stockpile_init_t":
        "Assumption: live (draw-down) capacity of the crushed-ore stockpile 25 kt (12.5 h of plant feed); "
        "the shift starts with 12 kt (6 h).",
    "faces.*":
        "Assumption: 15 m bench, 25 m cut width, so a face advances tonnes / (density x 15 x 25) metres. "
        "Blasted rock in front of the faces at shift start: 90 kt waste, 45 kt ore; the 06:00 blast "
        "adds 45 kt of waste and 25 kt of ore, a little above one shift of digging, so the blasted stock "
        "does not run out on the live stand where shifts follow each other.",
    "PIT_STAGES":
        "Assumption: vertical advance 15 m per year, so stages 0-3 are now, +2, +4 and +6 years "
        "(ore bench at -165, -195, -225, -255 m). Typical vertical advance of large open pits is 10-30 m/y.",
}
