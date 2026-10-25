"""Dispatch policies. A dispatcher answers one question each time a truck
becomes empty: which face does it go to next."""


OUT_OF_SERVICE = ("handover", "to_parking", "break", "to_fuel", "refuel", "to_workshop", "repair_queue",
                  "repair", "breakdown")


class FixedDispatcher:
    """Mode A: every truck is bound to one face for the whole shift."""

    name = "fixed"

    def __init__(self, faces_by_truck: dict[str, str]):
        self.faces_by_truck = faces_by_truck

    @classmethod
    def split(cls, trucks: list[str], n_lower: int) -> "FixedDispatcher":
        return cls({t: ("face-lower" if i < n_lower else "face-upper") for i, t in enumerate(trucks)})

    def assign(self, truck, model) -> str:
        return self.faces_by_truck[truck.name]


def cycle_s(model, face: str) -> float:
    """Mean truck cycle on a face, returning to the same face."""
    f = model.pit.faces[face]
    dump_min = model.p.crusher.dump_min if f.destination == "crusher" else model.p.site.waste_dump_min
    dozer = getattr(model, "dozer", None)
    if f.destination == "dump" and dozer is not None and dozer.state == "down":
        dump_min = tuple(x * model.p.support.dump_slowdown_without_dozer for x in dump_min)
    loaded = model.travel_cost(model.loaded_route(face), model.p.truck.payload_t)[0]
    empty = model.travel_cost(model.pit.empty_route(face, model.dest(face)), 0.0)[0]
    return model.mean_load_s(face) + loaded + sum(dump_min) / 2 * 60 + empty


def target_rates(model) -> dict[str, float]:
    """Upper stage: the fleet allocation that maximises tonnes per hour.
    With one fleet constraint and per-shovel caps the LP optimum is the greedy
    fill of faces in order of tonnes per truck-hour. A jammed crusher takes the
    ore face out of the plan: only the fact of the jam is known, not when it
    will be cleared."""
    payload = model.p.truck.payload_t
    now = model.env.now
    crusher_jammed = (model.crusher.stopped_until > now or not model.crusher.running
                      or getattr(model.crusher, "blocked", False))
    faces = []
    for face, shovel in model.shovels.items():
        if shovel.blocked_until > now:
            continue
        if crusher_jammed and model.pit.faces[face].destination == "crusher":
            continue
        c = cycle_s(model, face)
        faces.append((payload * 3600 / c, c / model.mean_load_s(face), face))
    rates = {face: 0.0 for face in model.shovels}
    trucks_left = float(sum(1 for t in model.trucks if t.state not in OUT_OF_SERVICE))
    for per_truck_tph, saturating_trucks, face in sorted(faces, reverse=True):
        n = min(saturating_trucks, trucks_left)
        rates[face] = n * per_truck_tph
        trucks_left -= n
    return rates


class DispatchDispatcher:
    """Mode B: two-stage dispatching (upper stage LP flow targets, lower stage
    'neediest shovel' - White & Olson, 1986, the scheme of Modular Mining
    DISPATCH). Targets are recomputed at every call, so a shovel that goes down
    or comes back changes the flows at once."""

    name = "dispatch"

    def __init__(self):
        self.last_alloc: dict[str, float] = {}

    def assign(self, truck, model) -> str:
        now = model.env.now
        rates = target_rates(model)
        payload = model.p.truck.payload_t
        best, best_slot, best_score = None, 0.0, None
        for face, rate in rates.items():
            if rate <= 0:
                continue
            interval = payload * 3600 / rate
            arrive = now + model.travel_cost(model.empty_route(truck, face), 0.0)[0]
            due = self.last_alloc.get(face, -interval) + interval
            score = due - arrive
            if best_score is None or score < best_score:
                best, best_slot, best_score = face, max(arrive, due), score
        if best is None:
            best = min(model.shovels, key=lambda f: model.shovels[f].blocked_until)
            best_slot = now
        self.last_alloc[best] = best_slot
        return best


class GreedyDispatcher:
    """Naive reference: minimise (truck waiting + shovel idle) for this truck only."""

    name = "greedy"

    def assign(self, truck, model) -> str:
        now = model.env.now
        best, best_cost = None, None
        for face, shovel in model.shovels.items():
            arrive = now + model.travel_cost(model.empty_route(truck, face), 0.0)[0]
            ready = self._shovel_ready(model, face, shovel, before=arrive)
            cost = abs(arrive - ready)
            if best_cost is None or cost < best_cost:
                best, best_cost = face, cost
        return best

    @staticmethod
    def _shovel_ready(model, face: str, shovel, before: float) -> float:
        now = model.env.now
        ready = max(now, shovel.blocked_until)
        if shovel.loading is not None:
            ready = max(ready, shovel.load_end)
        load_s = model.mean_load_s(face)
        committed = sorted(
            (now if t.state == "queued_at_shovel" else t.eta)
            for t in model.trucks
            if t.face == face and t.state in ("to_face", "queued_at_shovel")
        )
        for eta in committed:
            if eta > before:
                break
            ready = max(ready, eta) + load_s
        return ready
