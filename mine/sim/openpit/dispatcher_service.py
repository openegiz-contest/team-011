"""Module 1 service: truck dispatching over the platform.

It sees the mine only through Ditto: twin events of shovels and trucks keep a
mirror up to date, a 'dispatch-request' live message asks for a decision, and
the answer goes back as a 'dispatch-assign' live message. The decision is the
same DispatchDispatcher used in batch experiments.

    python -m openpit.dispatcher_service
"""

import logging
import signal
import sys
import time
from types import SimpleNamespace

from .bus import EVENTS, MESSAGES, Bus
from .dispatch import DispatchDispatcher, target_rates
from .knowledge import PitKnowledge
from .live import control_values
from .model import RAIN_ROLLING_RESISTANCE
from .params import Params, at_stage
from .twins import WHATIF, live_message, modify_features

log = logging.getLogger("dispatcher")

SERVICE = "dispatcher-01"


class Mirror(PitKnowledge):
    def __init__(self, params: Params):
        self._init_knowledge(params)
        self._base_rr = params.site.rolling_resistance
        self.env = SimpleNamespace(now=0.0)
        self.shovels = {
            face.name: SimpleNamespace(name=face.shovel, blocked_until=0.0, loading=None, load_end=0.0)
            for face in self.pit.faces.values()
        }
        self._by_shovel = {s.name: s for s in self.shovels.values()}
        self.trucks = [SimpleNamespace(name=f"truck-{i + 1:02d}", state="parked", face=None, eta=0.0,
                                       location="parking", bay=self.pit.bays[i], bay_exit=self.pit.bay_exits[i])
                       for i in range(params.site.trucks)]
        self._by_truck = {t.name: t for t in self.trucks}
        self.crusher = SimpleNamespace(stopped_until=0.0, running=True, blocked=False)
        self.dozer = SimpleNamespace(state="working")

    def update(self, name: str, features: dict) -> None:
        v = {k: (f.get("properties") or {}).get("value") for k, f in (features or {}).items()}
        if name == "crusher-01":
            self.crusher.stopped_until = float("inf") if v.get("state") == "jammed" else 0.0
            self.crusher.running = v.get("state") != "stopped"
            self.crusher.blocked = v.get("state") == "blocked"
        elif name == "pit-01" and v.get("pit_depth_m") is not None and -v["pit_depth_m"] != self.pit.lower_z:
            self.apply_whatif({"pit_stage": v.get("pit_stage") or 0})
        elif name == "dozer-01":
            self.dozer.state = v.get("state", self.dozer.state)
        elif name == "grader-01" and v.get("rolling_resistance"):
            self.set_rolling_resistance(float(v["rolling_resistance"]))
        elif name in self._by_truck:
            t = self._by_truck[name]
            t.state = v.get("state", t.state)
            t.face = v.get("face") or None
            t.eta = float(v.get("eta_s") or 0.0)
            t.location = v.get("location", t.location)
        elif name in self._by_shovel:
            s = self._by_shovel[name]
            s.blocked_until = float(v.get("blocked_until_s") or 0.0)
            s.load_end = float(v.get("load_end_s") or 0.0)
            s.loading = True if v.get("state") == "loading" else None

    def apply_whatif(self, values: dict) -> None:
        """Conditions the dispatcher must know to estimate travel times."""
        stage = values.get("pit_stage")
        if stage is not None and at_stage(Params(), stage).site.lower_bench_z != self.pit.lower_z:
            closed, rr = set(self.closed_roads), self.p.site.rolling_resistance
            self._init_knowledge(at_stage(Params(), stage))
            log.info("pit stage %s: ore bench at %.0f m", stage, self.pit.lower_z)
            self.closed_roads |= closed
            self.set_rolling_resistance(rr)
        if "road_dump_closed" in values:
            (self.closed_roads.add if values["road_dump_closed"] else self.closed_roads.discard)("road-dump")
        if "rain" in values:
            self.set_rolling_resistance(RAIN_ROLLING_RESISTANCE if values["rain"] else self._base_rr)


class DispatcherService:
    def __init__(self, bus=None):
        self.mirror = Mirror(Params())
        self.policy = DispatchDispatcher()
        self.bus = bus or Bus("pit-dispatcher", [f"{EVENTS}/#", f"{MESSAGES}/+"])
        self.decisions = 0
        self.last = {"truck": "", "face": ""}

    def decide(self, value: dict) -> None:
        now = float(value.get("sim_time_s", 0.0))
        if now < self.mirror.env.now:
            self.policy = DispatchDispatcher()
            log.info("new shift detected, dispatch schedule reset")
        self.mirror.env.now = now
        truck = self.mirror._by_truck.get(value.get("truck", ""))
        if truck is None:
            return
        truck.location, truck.state = value.get("location", truck.location), "awaiting_dispatch"
        face = self.policy.assign(truck, self.mirror)
        truck.face, truck.state = face, "to_face"
        truck.eta = now + self.mirror.travel_cost(self.mirror.empty_route(truck, face), 0.0)[0]
        cid = value.get("request_id", "")
        self.bus.send("dispatch", live_message(truck.name, "dispatch-assign",
                                               {"request_id": cid, "face": face}, cid))
        self.decisions += 1
        self.last = {"truck": truck.name, "face": face}
        log.info("t=%7.0fs %s -> %s", now, truck.name, face)

    def publish_status(self) -> None:
        rates = target_rates(self.mirror)
        features = {
            "decisions": {"value": self.decisions},
            "last_truck": {"value": self.last["truck"]},
            "last_face": {"value": self.last["face"]},
            "target_lower_tph": {"value": round(rates.get("face-lower", 0.0), 1), "unit": "t/h"},
            "target_upper_tph": {"value": round(rates.get("face-upper", 0.0), 1), "unit": "t/h"},
        }
        self.bus.send(SERVICE, modify_features(SERVICE, features))

    def run(self) -> None:
        self.bus.connect()
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        next_status = 0.0
        while not stop:
            for topic, msg in self.bus.drain():
                name = topic.rsplit("/", 1)[-1]
                if topic.startswith(EVENTS) and name == WHATIF:
                    self.mirror.apply_whatif(control_values(msg.get("path", ""), msg.get("value")))
                elif topic.startswith(EVENTS) and msg.get("path") == "/features":
                    self.mirror.update(name, msg.get("value"))
                elif topic.startswith(MESSAGES) and str(msg.get("topic", "")).endswith("/dispatch-request"):
                    self.decide(msg.get("value") or {})
            if time.monotonic() >= next_status:
                self.publish_status()
                next_status = time.monotonic() + 2.0
            time.sleep(0.02)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    DispatcherService().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
