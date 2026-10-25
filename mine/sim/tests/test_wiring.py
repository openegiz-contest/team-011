"""One shift of the live stand with the services, with MQTT and Ditto replaced
by an in-process router: what the simulator sends reaches the services the
way Ditto routes it (twin events and live messages), and their answers come
back the same way. Run from mine/sim:

    python -m tests.test_wiring
"""

import sys

from openpit.bus import EVENTS, MESSAGES
from openpit.dispatch import DispatchDispatcher
from openpit.dispatcher_service import DispatcherService
from openpit.live import LiveRunner
from openpit.model import MineModel
from openpit.params import Params
from openpit.safety_service import SafetyService


class Endpoint:
    def __init__(self, router):
        self.router, self.inbox = router, []

    def send(self, name, message):
        self.router.route(name, message)

    def drain(self):
        out, self.inbox = self.inbox, []
        return out


class Router:
    """Ditto in miniature: a modify of /features becomes a twin event, a live
    message is delivered to everyone subscribed to live messages."""

    def __init__(self):
        self.sim, self.dispatcher, self.safety = Endpoint(self), Endpoint(self), Endpoint(self)

    def route(self, name, message):
        if message.get("path") == "/features":
            self.dispatcher.inbox.append((f"{EVENTS}/{name}", message))
        elif "/live/messages/" in message.get("topic", ""):
            for endpoint in (self.sim, self.dispatcher, self.safety):
                endpoint.inbox.append((f"{MESSAGES}/{name}", message))


def pump(services):
    for service in services:
        for topic, msg in service.bus.drain():
            subject = str(msg.get("topic", "")).rsplit("/", 1)[-1]
            value = msg.get("value") or {}
            if isinstance(service, DispatcherService):
                if topic.startswith(EVENTS):
                    service.mirror.update(topic.rsplit("/", 1)[-1], msg.get("value"))
                elif subject == "dispatch-request":
                    service.decide(value)
            elif subject == "oversize-detected":
                service.on_oversize(value)
            elif subject == "gas-report":
                service.on_gas(value)


def run_live(seed: int, modes: tuple[str, str, str], step_s: float = 30.0) -> LiveRunner:
    router = Router()
    runner = LiveRunner(seed, 1.0, bus=router.sim)
    runner.modes.update(dispatch_mode=modes[0], oversize_mode=modes[1], blast_mode=modes[2])
    services = [DispatcherService(bus=router.dispatcher), SafetyService(bus=router.safety)]
    runner.start_shift(0)
    shift_s = runner.params.site.shift_h * 3600
    while runner.model.env.now < shift_s:
        runner.model.env.run(until=min(shift_s, runner.model.env.now + step_s))
        runner.publish()
        pump(services)
        runner.handle_inbox()
    return runner


def check_scenarios(seed: int = 1) -> bool:
    """What-if scenarios switched on and undone mid-shift on the live stand."""
    router = Router()
    runner = LiveRunner(seed, 1.0, bus=router.sim)
    services = [DispatcherService(bus=router.dispatcher), SafetyService(bus=router.safety)]
    runner.start_shift(0)
    model, dispatcher = runner.model, services[0]
    h = 3600
    timeline = {1 * h: {"blast_now": 1}, 4 * h: {"road_dump_closed": True, "shovel_01_down": True},
                6 * h: {"road_dump_closed": False, "shovel_01_down": False, "trucks_in_repair": 2},
                8 * h: {"trucks_in_repair": 0}}
    seen = {}
    while model.env.now < 10 * h:
        model.env.run(until=model.env.now + 30.0)
        for t, change in list(timeline.items()):
            if model.env.now >= t:
                runner.apply_whatif(change)
                dispatcher.mirror.apply_whatif(change)
                del timeline[t]
        runner.publish()
        pump(services)
        runner.handle_inbox()
        now = model.env.now
        if 5 * h <= now < 5 * h + 30:
            seen["shovel_01_at_5h"] = model.shovels["face-upper"].state
            seen["dump2_t_at_5h"] = round(model.metrics.tonnes_by_dump["dump-2"])
        if 7 * h <= now < 7 * h + 30:
            seen["in_repair_at_7h"] = sum(model._in_repair(t) for t in model.trucks)
            seen["at_workshop_at_7h"] = sum(model._in_repair(t) and t.state in ("repair", "to_workshop")
                                            for t in model.trucks)
            seen["shovel_01_at_7h"] = model.shovels["face-upper"].state
            seen["dump2_t_at_7h"] = round(model.metrics.tonnes_by_dump["dump-2"])
        if 9 * h <= now < 9 * h + 30:
            seen["in_repair_at_9h"] = sum(model._in_repair(t) for t in model.trucks)
            seen["dump2_t_at_9h"] = round(model.metrics.tonnes_by_dump["dump-2"])
    seen["blast_t_h"] = round(model.blast.t_blast / h, 2) if model.blast.t_blast else None
    print("scenarios on the live stand:", seen)
    return (seen["shovel_01_at_5h"] == "stopped" and seen["dump2_t_at_5h"] > 0
            and seen["in_repair_at_7h"] == 2 and seen["at_workshop_at_7h"] >= 1
            and seen["shovel_01_at_7h"] != "stopped"
            and seen["in_repair_at_9h"] == 0 and seen["dump2_t_at_9h"] == seen["dump2_t_at_7h"]
            and seen["blast_t_h"] < 1.5)


def check_plant_and_stage(seed: int = 1) -> bool:
    """Concentrator stopped until the stockpile fills and blocks the crusher;
    then the pit is moved six years ahead, which restarts the shift on the new
    layout and publishes it."""
    router = Router()
    sent = []
    route = router.route
    router.route = lambda name, message: (sent.append(message), route(name, message))
    runner = LiveRunner(seed, 1.0, bus=router.sim)
    services = [DispatcherService(bus=router.dispatcher), SafetyService(bus=router.safety)]
    runner.start_shift(0)
    dispatcher = services[0]
    change = {"plant_stop": True}
    runner.apply_whatif(change)
    dispatcher.mirror.apply_whatif(change)
    seen = {"mirror_blocked": False}
    while runner.model.env.now < 11 * 3600 and not runner.model.crusher.blocked:
        runner.model.env.run(until=runner.model.env.now + 30.0)
        runner.publish()
        pump(services)
        runner.handle_inbox()
        seen["mirror_blocked"] |= dispatcher.mirror.crusher.blocked
    seen["blocked_at_h"] = round(runner.model.env.now / 3600, 2)
    seen["stockpile_t"] = round(runner.model.plant.stockpile_t)
    seen["face_moved"] = runner.model.pit.faces["face-lower"].position != runner.model.pit.face_position("face-lower", 0.0)
    seen["ore_face_advance_m"] = round(runner.model.advance_m("face-lower"), 1)
    change = {"pit_stage": 3}
    runner.apply_whatif(change)
    dispatcher.mirror.apply_whatif(change)
    seen["depth_after"] = runner.model.pit.lower_z
    seen["sim_time_after"] = runner.model.env.now
    seen["mirror_depth"] = dispatcher.mirror.pit.lower_z
    seen["geometry_sent"] = any(m.get("path") == "/attributes/geometry" and m["value"]["pit"]["benches_z"][-1] == -255
                                for m in sent)
    print("plant and stage:", seen)
    return (seen["blocked_at_h"] < 11 and seen["mirror_blocked"] and seen["stockpile_t"] >= runner.model.plant.capacity_t - 100
            and seen["face_moved"] and seen["depth_after"] == -255 and seen["mirror_depth"] == -255
            and seen["sim_time_after"] == 0 and seen["geometry_sent"])


def check_advisor(seed: int = 1) -> bool:
    """The advisor sees the twins as Ditto publishes them and names the
    right action: the drone before the blast in mode A, the stockpile when
    the plant is stopped, nothing to switch once everything is in mode B."""
    from openpit.advisor_service import advise
    from openpit.twins import snapshot

    def twins(model, modes, whatif):
        snap = snapshot(model, modes, 0)
        out = {name: {k: f.get("value") for k, f in features.items()} for name, features in snap.items()}
        out["control-01"], out["whatif-01"] = dict(modes), dict(whatif)
        return out

    modes_a = {"dispatch_mode": "A", "oversize_mode": "A", "blast_mode": "A"}
    whatif = {"plant_stop": True}
    model = MineModel(Params(), seed, DispatchDispatcher(), modes={"oversize": "A", "blast": "A"}, whatif=whatif)
    seen = {}
    for h in (5.5, 7.0, 11.0):
        model.env.run(until=h * 3600)
        seen[h] = [r["id"] for r in advise(twins(model, modes_a, whatif))]
    modes_b = {"dispatch_mode": "B", "oversize_mode": "B", "blast_mode": "B"}
    seen["B"] = [r["id"] for r in advise(twins(model, modes_b, {})) if r["action"]]
    print("advisor:", seen)
    return ("drone-before-blast" in seen[5.5] and "gas-manual" in seen[7.0]
            and ({"stockpile-full", "stockpile-filling"} & set(seen[11.0])) and not seen["B"])


def check_shift_logic(seed: int = 1) -> bool:
    """The order of a shift: meal taken at the park-up while the faces are
    closed for the blast, nobody left at a closed face, fuel by tank level,
    the dump slows down without its dozer, the next shift starts where this
    one ended, and the advisor keeps a history of its advice."""
    import math
    from openpit.advisor_service import AdvisorService
    p = Params()
    model = MineModel(p, seed, DispatchDispatcher(), modes={"oversize": "B", "blast": "A"})
    t_evac = p.blasting.blast_hour * 3600 - p.blasting.evacuation_min * 60
    model.env.run(until=t_evac + 600)
    at_faces = [t.name for t in model.trucks if t.location in model.pit.faces and t.route is None]
    # After ~10 min of evacuation the shovels must have left the face for a rim pad.
    shovels_away = all(
        math.dist(model.shovels[f].xy[:2], model.pit.faces[f].position[:2]) > 80
        and model.shovels[f].state in ("to_safe", "evacuated")
        for f in model.shovels)
    parking = model.pit.points["parking"]
    model.env.run(until=t_evac + 1800)
    eating = [t for t in model.trucks if t.state == "break"]
    near = all(math.dist(model.truck_xy(t)[:2], parking[:2]) < 200 for t in eating)
    model.env.run(until=p.blasting.blast_hour * 3600 + 3 * 3600)
    meals = sum(t.meal_done for t in model.trucks)
    # Zones reopen after re-entry: shovels walk back and dig again.
    shovels_back = all(
        math.dist(model.shovels[f].xy[:2], model.pit.faces[f].position[:2]) < 40
        and model.shovels[f].state not in ("to_safe", "evacuated", "to_face")
        for f in model.shovels)
    m = model.run()
    carry = model.carry_out()
    nxt = MineModel(p, seed + 1, DispatchDispatcher(), carry=carry)
    carried = (abs(nxt.plant.stockpile_t - min(carry["stockpile_t"], nxt.plant.capacity_t)) < 1
               and nxt.advance_m("face-lower") == carry["advance_m"]["face-lower"]
               and all(nxt.trucks[i].fails_at == 0.0 for i, t in enumerate(model.trucks) if t.name in carry["broken"]))
    dz = MineModel(p, seed, DispatchDispatcher(), whatif={"dozer_down": True})
    dz.env.run(until=3 * 3600)
    dozer = dz.dozer.state == "down" and dz.dumps["dump"].count + len(dz.dumps["dump"].queue) >= 1

    class Quiet:
        def send(self, *_):
            pass
    adv = AdvisorService(bus=Quiet())
    adv.twins = {"pit-01": {"sim_time_s": 100.0}, "whatif-01": {"plant_stop": True},
                 "plant-01": {"state": "stopped"}, "stockpile-01": {"fill": 0.9}}
    adv.publish()
    adv.twins["whatif-01"]["plant_stop"] = False
    adv.twins["plant-01"]["state"], adv.twins["pit-01"]["sim_time_s"] = "running", 400.0
    adv.publish()
    hist = adv.history
    history = bool(hist) and hist[0]["until"] is not None and hist[0]["applied"] is True
    res = {"at_faces_after_evacuation": at_faces, "shovels_away": shovels_away, "shovels_back": shovels_back,
           "eating_at_parking": f"{len(eating)} near={near}",
           "meals": meals, "refuels": m.refuels, "evacuated": m.evacuated, "carried": carried,
           "dozer": dozer, "history": history}
    print("shift logic:", res)
    return (not at_faces and shovels_away and shovels_back and eating and near
            and meals == len(model.trucks) and carried and dozer and history)


def main() -> int:
    seed = 1
    runner = run_live(seed, ("B", "B", "B"))
    m = runner.model.metrics
    fallbacks = runner.dispatcher.fallbacks + runner.guard.fallbacks + runner.reentry.fallbacks
    batch = MineModel(Params(), seed, DispatchDispatcher(), modes={"oversize": "B", "blast": "B"}).run()

    def row(x):
        return (round(x.total_t), x.boulders, x.jams, x.rockbreaks, round(x.face_closed_s.get("face-lower", 0) / 60))

    print("               total t, boulders, jams, rockbreaks, ore face shut min")
    print("live, routed  ", row(m), "service fallbacks", fallbacks)
    print("batch model   ", row(batch))
    ok = fallbacks == 0 and m.face_closed_s and runner.reentry.local_b.decisions == 0
    ok = check_scenarios(seed) and ok
    ok = check_plant_and_stage(seed) and ok
    ok = check_advisor(seed) and ok
    ok = check_shift_logic(seed) and ok
    from openpit.twins import definitions
    things = definitions(MineModel(Params(), 0, DispatchDispatcher()))
    print("twin definitions:", len(things))
    ok = len(things) > 30 and ok
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
