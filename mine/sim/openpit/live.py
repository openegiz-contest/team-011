"""Live stand: runs the shift model in real time (x speedup) and mirrors it into
Ditto over MQTT. Modes come from the control-01 twin; in dispatch mode B each
empty truck asks the dispatcher service through a Ditto live message.

    python -m openpit.live
"""

import json
import logging
import os
import signal
import sys
import time
import uuid

from .bus import EVENTS, MESSAGES, Bus, Ditto
from .dispatch import FixedDispatcher
from .model import WHATIF_DEFAULTS, MineModel
from .params import Params
from .safety import CrusherGuard, drone_reentry, manual_reentry
from .twins import CONTROL, DEFAULT_CONTROL, WHATIF, NAMESPACE, live_message, modify_features, modify_geometry, snapshot, thing_id

log = logging.getLogger("live")

FIXED_LOWER = 5
DISPATCH_TIMEOUT_S = 120.0


class LiveDispatcher:
    """Mode A answers locally (fixed assignment). Mode B asks over Ditto."""

    def __init__(self, runner: "LiveRunner"):
        self.runner = runner
        self.fixed = None
        self.pending: dict[str, tuple] = {}
        self.fallbacks = 0

    def request(self, truck, model):
        if self.fixed is None:
            self.fixed = FixedDispatcher.split([t.name for t in model.trucks], FIXED_LOWER)
        event = model.env.event()
        if self.runner.modes["dispatch_mode"] != "B":
            event.succeed(self.fixed.assign(truck, model))
            return event
        cid = uuid.uuid4().hex[:12]
        self.pending[cid] = (event, truck)
        self.runner.bus.send("dispatch", live_message(truck.name, "dispatch-request", {
            "request_id": cid, "truck": truck.name, "location": truck.location,
            "sim_time_s": round(model.env.now, 1), "shift": self.runner.shift}, cid))
        model.env.process(self._timeout(model, cid))
        return event

    def _timeout(self, model, cid):
        yield model.env.timeout(DISPATCH_TIMEOUT_S)
        entry = self.pending.pop(cid, None)
        if entry and not entry[0].triggered:
            self.fallbacks += 1
            log.warning("no dispatch answer for %s, fixed fallback", entry[1].name)
            entry[0].succeed(self.fixed.assign(entry[1], model))

    def answer(self, cid: str, face: str, model) -> None:
        entry = self.pending.pop(cid, None)
        if entry and not entry[0].triggered and face in model.pit.faces:
            entry[0].succeed(face)


class RemotePolicy:
    """Module 2 and 3 decisions over Ditto: the question goes to `thing` as a
    live message, the service answers with `reply`. A local policy with the
    same rule answers when the service is silent, or always in mode A."""

    def __init__(self, runner: "LiveRunner", mode_key: str, thing: str, ask: str, reply: str,
                 local_a, local_b, answer_key: str):
        self.runner, self.mode_key, self.thing, self.ask, self.reply = runner, mode_key, thing, ask, reply
        self.local_a, self.local_b, self.answer_key = local_a, local_b, answer_key
        self.pending: dict[str, tuple] = {}
        self.fallbacks = 0

    def request(self, message: dict, model):
        event = model.env.event()
        if self.runner.modes[self.mode_key] != "B":
            event.succeed(self.local_a.decide(message))
            return event
        cid = uuid.uuid4().hex[:12]
        self.pending[cid] = (event, message)
        self.runner.bus.send(self.thing, live_message(self.thing, self.ask, {
            **message, "request_id": cid, "sim_time_s": round(model.env.now, 1), "shift": self.runner.shift}, cid))
        model.env.process(self._timeout(model, cid))
        return event

    def _timeout(self, model, cid):
        yield model.env.timeout(DISPATCH_TIMEOUT_S)
        entry = self.pending.pop(cid, None)
        if entry and not entry[0].triggered:
            self.fallbacks += 1
            log.warning("no %s answer, local fallback", self.reply)
            entry[0].succeed(self.local_b.decide(entry[1]))

    def answer(self, value: dict) -> None:
        entry = self.pending.pop(value.get("request_id", ""), None)
        if entry and not entry[0].triggered:
            entry[0].succeed(value.get(self.answer_key))


def control_values(path: str, value) -> dict:
    """Feature values changed by a Ditto event on the control twin."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        value, parts = value.get("features", {}), ["features"]
    if parts == ["features"]:
        return {f: (v.get("properties", {}) or {}).get("value") for f, v in (value or {}).items()}
    if len(parts) >= 2 and parts[0] == "features":
        feature, rest = parts[1], parts[2:]
        if rest == ["properties", "value"]:
            return {feature: value}
        if rest == ["properties"]:
            return {feature: (value or {}).get("value")}
        if rest == []:
            return {feature: ((value or {}).get("properties") or {}).get("value")}
    return {}


class LiveRunner:
    def __init__(self, seed: int, interval: float, bus=None):
        self.params = Params()
        self.base_seed = seed
        self.interval = interval
        self.modes = {k: v["value"] for k, v in DEFAULT_CONTROL.items()}
        self.shift = 0
        self.run_counter = None
        self.last_shift: dict = {}
        self.whatif = dict(WHATIF_DEFAULTS)
        self.bus = bus or Bus("pit-live", [f"{EVENTS}/{CONTROL}", f"{EVENTS}/{WHATIF}", f"{MESSAGES}/+"])
        self.model = None
        self.dispatcher = None
        self.geometry_stage = None
        # Last published twin features — skip MQTT when nothing changed (cuts
        # Ditto/connectivity load that otherwise freezes the UI at high speedup).
        self._last_snap: dict[str, dict] = {}
        # State the next shift starts from; a restart repeats the shift from
        # the same state, a change of stage starts afresh.
        self.carry_next: dict | None = None
        self.carry_in: dict | None = None
        self.carry_shift: int | None = None

    def load_control(self) -> None:
        if "DITTO_PASSWORD" not in os.environ:
            return
        try:
            _, features = Ditto().request("GET", f"/things/{thing_id(CONTROL)}/features")
            self.apply_control(control_values("/features", features), initial=True)
            _, features = Ditto().request("GET", f"/things/{thing_id(WHATIF)}/features")
            self.apply_whatif(control_values("/features", features))
        except Exception as exc:
            log.warning("could not read %s / %s (%s), using defaults", CONTROL, WHATIF, exc)

    def apply_whatif(self, values: dict) -> None:
        for key, value in values.items():
            if key not in WHATIF_DEFAULTS or value is None or value == self.whatif.get(key):
                continue
            self.whatif[key] = value
            log.info("what-if %s = %s", key, value)
            if self.model is None:
                continue
            if key == "pit_stage":
                self.carry_next = self.carry_in = None
                self.carry_shift = None
                self.start_shift(self.shift)
            else:
                self.model.set_whatif(key, value)

    def apply_control(self, values: dict, initial: bool = False) -> None:
        restart = False
        for key, value in values.items():
            if key == "run":
                if self.run_counter is not None and value != self.run_counter:
                    restart = True
                self.run_counter = value
            if key in self.modes and value is not None:
                self.modes[key] = value
        log.info("modes %s%s", self.modes, " (restart shift)" if restart else "")
        if self.model is not None:
            self.model.modes.update(self.model_modes())
        if restart and not initial:
            self.start_shift(self.shift)

    def model_modes(self) -> dict:
        return {"oversize": self.modes["oversize_mode"], "blast": self.modes["blast_mode"]}

    def start_shift(self, shift: int) -> None:
        self.shift = shift
        self.dispatcher = LiveDispatcher(self)
        p = self.params
        self.guard = RemotePolicy(self, "oversize_mode", "crusher-01", "oversize-detected", "rockbreak-command",
                                  CrusherGuard(p), CrusherGuard(p), "action")
        self.reentry = RemotePolicy(self, "blast_mode", "drone-01", "gas-report", "reentry-decision",
                                    manual_reentry(p), drone_reentry(p), "permit")
        held = {k: v for k, v in self.whatif.items() if k != "blast_now"}
        if self.carry_shift != shift:
            self.carry_in, self.carry_shift = self.carry_next, shift
        self.model = MineModel(p, self.base_seed + shift, self.dispatcher, modes=self.model_modes(),
                               guard=self.guard, reentry=self.reentry, whatif=held, carry=self.carry_in)
        self.model.whatif["blast_now"] = self.whatif["blast_now"]
        if self.geometry_stage != self.model.stage:
            self.geometry_stage = self.model.stage
            self.bus.send("pit-01", modify_geometry(self.model))
        log.info("shift %d started (seed %d) with %s", shift, self.base_seed + shift, self.modes)

    def finish_shift(self) -> None:
        m = self.model.metrics
        self.carry_next = self.model.carry_out()
        self.last_shift = {
            "shift": self.shift, "seed": self.base_seed + self.shift,
            "dispatch_mode": self.modes["dispatch_mode"], "oversize_mode": self.modes["oversize_mode"],
            "blast_mode": self.modes["blast_mode"], "total_t": round(m.total_t),
            "ore_t": round(m.ore_t), "waste_t": round(m.waste_t), "queue_h": round(m.truck_queue_s / 3600, 2),
            "l_per_t_ore": round(m.fuel_by_material_l["ore"] / m.ore_t, 4) if m.ore_t else None,
            "l_per_t_waste": round(m.fuel_by_material_l["waste"] / m.waste_t, 4) if m.waste_t else None,
            "dispatch_fallbacks": self.dispatcher.fallbacks,
            "crusher_down_min": round(m.crusher_down_s / 60, 1), "jams": m.jams, "rockbreaks": m.rockbreaks,
            "ore_face_shut_min": round(m.face_closed_s.get("face-lower", 0) / 60, 1),
            "gas_exposure_min": round(m.gas_exposure_min, 1),
            "wind_ms": round(m.wind_ms, 1) if m.wind_ms == m.wind_ms else None,
            "service_fallbacks": self.guard.fallbacks + self.reentry.fallbacks,
            "plant_t": round(m.plant_t), "plant_idle_min": round((m.plant_starved_s + m.plant_stopped_s) / 60, 1),
            "stockpile_t": round(self.model.plant.stockpile_t), "pit_stage": self.model.stage,
            "cu_ore_t": round(m.cu_ore_t, 1), "cu_plant_t": round(m.cu_plant_t, 1),
            "refuels": m.refuels, "breakdowns": m.breakdowns, "evacuated": m.evacuated,
            "carried_broken": self.carry_next["broken"],
        }
        log.info("shift %d finished: %s", self.shift, self.last_shift)

    def handle_inbox(self) -> None:
        for topic, msg in self.bus.drain():
            if topic == f"{EVENTS}/{CONTROL}":
                self.apply_control(control_values(msg.get("path", ""), msg.get("value")))
            elif topic == f"{EVENTS}/{WHATIF}":
                self.apply_whatif(control_values(msg.get("path", ""), msg.get("value")))
            elif topic.startswith(MESSAGES):
                subject = str(msg.get("topic", "")).rsplit("/", 1)[-1]
                value = msg.get("value") or {}
                if subject == "dispatch-assign":
                    self.dispatcher.answer(value.get("request_id", ""), value.get("face", ""), self.model)
                elif subject == self.guard.reply:
                    self.guard.answer(value)
                elif subject == self.reentry.reply:
                    self.reentry.answer(value)

    def publish(self) -> None:
        snap = snapshot(self.model, self.modes, self.shift)
        snap["pit-01"]["last_shift"] = {"value": json.dumps(self.last_shift)}
        for name, features in snap.items():
            if self._last_snap.get(name) == features:
                continue
            self.bus.send(name, modify_features(name, features))
            self._last_snap[name] = features

    def run(self) -> None:
        self.bus.connect()
        self.load_control()
        self.start_shift(0)
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        shift_s = self.params.site.shift_h * 3600
        while not stop:
            t0 = time.monotonic()
            self.handle_inbox()
            step = self.interval * float(self.modes.get("speedup") or 30)
            self.model.env.run(until=min(shift_s, self.model.env.now + step))
            self.publish()
            if self.model.env.now >= shift_s:
                self.finish_shift()
                self.publish()
                self.start_shift(self.shift + 1)
            time.sleep(max(0.0, self.interval - (time.monotonic() - t0)))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    seed = int(os.environ.get("SIM_SEED", "1"))
    interval = float(os.environ.get("SIM_INTERVAL", "1"))
    log.info("namespace %s, seed %d, interval %.1fs", NAMESPACE, seed, interval)
    LiveRunner(seed, interval).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
