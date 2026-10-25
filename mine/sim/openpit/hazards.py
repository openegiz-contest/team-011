"""Physics of modules 2 and 3: oversize blocks in a truckload and the
post-blast gas cloud. Pure functions, no simulation state."""

import math
import random

from .params import Blasting, Fragmentation


def oversize_fraction(x50_m: float, frag: Fragmentation, feed_opening_mm: float) -> float:
    """Mass fraction above the bridging size, Rosin-Rammler (Kuz-Ram)."""
    threshold_m = frag.oversize_ratio * feed_opening_mm / 1000.0
    return math.exp(-math.log(2) * (threshold_m / x50_m) ** frag.uniformity_n)


def poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def boulders_in_load(rng: random.Random, payload_t: float, x50_m: float, frag: Fragmentation,
                     feed_opening_mm: float) -> int:
    lam = oversize_fraction(x50_m, frag, feed_opening_mm) * payload_t / frag.boulder_mean_t
    return poisson(rng, lam)


def draw_x50(rng: random.Random, frag: Fragmentation) -> float:
    sigma = math.sqrt(math.log(1 + frag.x50_cv ** 2))
    return frag.x50_m * math.exp(rng.gauss(-sigma ** 2 / 2, sigma))


def draw_wind(rng: random.Random, b: Blasting) -> float:
    return max(b.wind_min_ms, rng.weibullvariate(b.wind_weibull_scale_ms, b.wind_weibull_k))


def time_constant_s(wind_ms: float, b: Blasting) -> float:
    return b.tau_ref_min * 60.0 * b.wind_ref_ms / wind_ms


def draw_peaks(rng: random.Random, b: Blasting) -> dict[str, float]:
    sigma = math.sqrt(math.log(1 + b.peak_cv ** 2))
    return {gas: median * math.exp(rng.gauss(0.0, sigma)) for gas, median in b.peak_median_mg_m3}


def concentration(peak: float, zone_factor: float, t_s: float, tau_s: float) -> float:
    return peak * zone_factor * math.exp(-t_s / tau_s)


def safe_time_s(peaks: dict[str, float], zone_factor: float, tau_s: float, b: Blasting) -> float:
    """Seconds after the blast until every gas in the zone is below MAC."""
    pdk = dict(b.pdk_mg_m3)
    worst = 0.0
    for gas, peak in peaks.items():
        ratio = peak * zone_factor / pdk[gas]
        if ratio > 1:
            worst = max(worst, tau_s * math.log(ratio))
    return worst


def drone_permit_s(safe_s: float, b: Blasting) -> float:
    """Permit time when the zone is cleared by the first full drone sweep that
    finds it below MAC, never before the legal minimum."""
    start = b.drone_start_min * 60.0
    sweep = b.drone_cells * b.drone_cell_s
    k = max(1, math.ceil((safe_s - start) / sweep))
    return max(b.legal_min_reentry_min * 60.0, start + k * sweep)
