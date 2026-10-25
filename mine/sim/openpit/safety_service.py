"""Module 2 and 3 services over the platform.

crusher-guard-01: the dump-pocket camera reports a block ('oversize-detected'
live message to crusher-01); the guard answers 'rockbreak-command'.
blast-control-01: after each drone sweep the gas readings of a closed zone
arrive as 'gas-report' to drone-01; the service answers 'reentry-decision'.
The rules are the same objects the batch experiments use (openpit.safety).

    python -m openpit.safety_service
"""

import logging
import signal
import sys
import time

from .bus import MESSAGES, Bus
from .params import Params
from .safety import CrusherGuard, drone_reentry
from .twins import live_message, modify_features

log = logging.getLogger("safety")


class SafetyService:
    def __init__(self, bus=None):
        params = Params()
        self.guard = CrusherGuard(params)
        self.reentry = drone_reentry(params)
        self.bus = bus or Bus("pit-safety", [f"{MESSAGES}/+"])
        self.guard_state = {"rockbreaks": 0, "last_size_m": 0.0}
        self.blast_state = {"permits": 0, "last_zone": "", "last_decision": ""}

    def reply(self, thing: str, subject: str, request: dict, **answer) -> None:
        cid = request.get("request_id", "")
        self.bus.send(thing, live_message(thing, subject, {"request_id": cid, **answer}, cid))

    def on_oversize(self, value: dict) -> None:
        action = self.guard.decide(value)
        self.guard_state["last_size_m"] = value.get("size_m", 0.0)
        self.guard_state["rockbreaks"] += action == "rockbreak"
        self.reply("crusher-01", "rockbreak-command", value, action=action)
        log.info("t=%7.0fs block %.2f m from %s -> %s", value.get("sim_time_s", 0), value.get("size_m", 0),
                 value.get("truck", "?"), action)

    def on_gas(self, value: dict) -> None:
        permit = self.reentry.decide(value)
        zone = value.get("zone", "")
        self.blast_state.update(last_zone=zone, last_decision="open" if permit else "keep closed")
        self.blast_state["permits"] += permit
        self.reply("drone-01", "reentry-decision", value, permit=permit, zone=zone)
        log.info("t=%7.0fs %s %.0f min after blast, readings %s -> %s", value.get("sim_time_s", 0), zone,
                 value.get("t_since_blast_s", 0) / 60, value.get("readings"), "open" if permit else "closed")

    def publish_status(self) -> None:
        self.bus.send("crusher-guard-01", modify_features("crusher-guard-01", {
            "decisions": {"value": self.guard.decisions},
            "rockbreaks": {"value": self.guard_state["rockbreaks"]},
            "last_size_m": {"value": self.guard_state["last_size_m"], "unit": "m"},
            "threshold_m": {"value": round(self.guard.threshold_m, 3), "unit": "m"},
        }))
        self.bus.send("blast-control-01", modify_features("blast-control-01", {
            "decisions": {"value": self.reentry.decisions},
            "permits": {"value": self.blast_state["permits"]},
            "last_zone": {"value": self.blast_state["last_zone"]},
            "last_decision": {"value": self.blast_state["last_decision"]},
        }))

    def run(self) -> None:
        self.bus.connect()
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        handlers = {"oversize-detected": self.on_oversize, "gas-report": self.on_gas}
        next_status = 0.0
        while not stop:
            for _, msg in self.bus.drain():
                handler = handlers.get(str(msg.get("topic", "")).rsplit("/", 1)[-1])
                if handler:
                    handler(msg.get("value") or {})
            if time.monotonic() >= next_status:
                self.publish_status()
                next_status = time.monotonic() + 2.0
            time.sleep(0.02)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    SafetyService().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
