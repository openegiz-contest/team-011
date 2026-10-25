"""Decision policies of modules 2 and 3. The model asks them the same way it
asks the dispatcher: `decide(...)` answers at once, `request(...)` returns a
SimPy event (live services over Ditto)."""

from .params import Params


class CrusherGuard:
    """Module 2: the camera reports a block; break it before it enters the crusher
    if it is larger than the bridging size."""

    def __init__(self, params: Params):
        f = params.fragmentation
        self.threshold_m = f.oversize_ratio * params.crusher.feed_opening_mm / 1000.0
        self.decisions = 0

    def decide(self, detection: dict) -> str:
        self.decisions += 1
        return "rockbreak" if detection["size_m"] >= self.threshold_m else "pass"


class Reentry:
    """Module 3: open a zone when the reported gas readings are below the margin
    of MAC, never before the legal minimum after the blast. Mode A gets hand
    samples from the rescue crew, mode B a full drone sweep."""

    def __init__(self, params: Params, margin: float):
        b = params.blasting
        self.pdk = dict(b.pdk_mg_m3)
        self.legal_s = b.legal_min_reentry_min * 60.0
        self.margin = margin
        self.decisions = 0

    def decide(self, report: dict) -> bool:
        self.decisions += 1
        if report["t_since_blast_s"] < self.legal_s:
            return False
        return all(value < self.margin * self.pdk[gas] for gas, value in report["readings"].items())


def manual_reentry(params: Params) -> Reentry:
    return Reentry(params, margin=1.0)


def drone_reentry(params: Params) -> Reentry:
    return Reentry(params, margin=0.8)
