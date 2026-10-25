"""Shift advisor (advisor-01): watches the twins and tells the shift who should
do what now, with the effect measured on replicated shifts. Where the action
is a change of a stand setting, it carries that change so the operator can
apply it in one click.

    python -m openpit.advisor_service
"""

import json
import logging
import signal
import sys
import time

from .bus import EVENTS, Bus
from .live import control_values
from .params import Params
from .twins import CONTROL, WHATIF, modify_features

log = logging.getLogger("advisor")

SERVICE = "advisor-01"

# Paired differences against mode A, 20 shifts (python -m openpit.experiment --shifts 20).
EFFECT = {
    "dispatch": "+2 % тоннажа и +10 % руды за смену, очереди самосвалов −17 %",
    "oversize": "простой дробилки −20 мин за смену",
    "drone": "забои открываются в среднем на 25 мин раньше, +7,8 % тоннажа, нет допуска при газе выше ПДК",
}
LEVELS = {"critical": 0, "warn": 1, "info": 2}


def _clock(seconds: float) -> str:
    return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}"


def _act(thing: str, feature: str, value, label: str) -> dict:
    return {"thing": thing, "feature": feature, "value": value, "label": label}


def advise(twins: dict, params: Params | None = None) -> list[dict]:
    """Recommendations from the current twin features ({twin: {feature: value}})."""
    p = params or Params()
    pit, ctl, wi = twins.get("pit-01", {}), twins.get(CONTROL, {}), twins.get(WHATIF, {})
    now = float(pit.get("sim_time_s") or 0.0)
    out = []

    def add(rule, level, who, text, effect="", action=None):
        out.append({"id": rule, "level": level, "who": who, "text": text, "effect": effect, "action": action})

    phase = pit.get("blast_phase")
    blast_s = p.blasting.blast_hour * 3600
    if phase == "planned" and ctl.get("blast_mode") != "B" and blast_s - 3600 <= now < blast_s:
        add("drone-before-blast", "warn", "Начальник смены → оператор дрона",
            f"Взрыв в {_clock(blast_s)}, через {int((blast_s - now) // 60)} мин. Подготовить дрон: допуск в рудный "
            "забой по его замерам газа (режим Б), а не по регламенту через 120 мин.",
            EFFECT["drone"], _act(CONTROL, "blast_mode", "B", "Допуск по дрону"))
    if phase == "gas" and ctl.get("blast_mode") != "B":
        add("gas-manual", "info", "Горноспасатели (ВГСЧ)",
            f"Газ после взрыва, допуск по регламенту: первый ручной замер в {_clock(blast_s + p.blasting.fixed_reentry_min * 60)}. "
            "Со следующего взрыва — замер дроном.",
            EFFECT["drone"], _act(CONTROL, "blast_mode", "B", "Допуск по дрону со следующего взрыва"))
    if (pit.get("gas_exposure_min") or 0) > 0:
        add("gas-exposure", "critical", "Начальник смены, служба охраны труда",
            f"Люди допущены в забой при газе выше ПДК ({pit['gas_exposure_min']:.1f} мин за смену): ручной замер "
            "не охватил всю зону. Вывести людей до повторного замера.",
            EFFECT["drone"], _act(CONTROL, "blast_mode", "B", "Допуск по дрону"))

    queues = {s: twins.get(s, {}) for s in ("shovel-01", "shovel-02")}
    busy = {s: (v.get("queue") or 0) for s, v in queues.items()}
    idle = [s for s, v in queues.items() if v.get("state") == "idle" and not busy[s]]
    long = [s for s, q in busy.items() if q >= 2]
    if ctl.get("dispatch_mode") != "B" and long and idle:
        add("rebalance", "warn", "Диспетчер",
            f"У {long[0]} очередь {busy[long[0]]} самосвала, {idle[0]} простаивает. "
            "Перевести самосвалы с очереди на простаивающий экскаватор: включить сервис диспетчера.",
            EFFECT["dispatch"], _act(CONTROL, "dispatch_mode", "B", "Включить диспетчер"))

    for s, v in queues.items():
        if v.get("state") in ("down", "stopped"):
            face = "вскрыши" if s == "shovel-01" else "руды"
            add(f"{s}-down", "warn", "Механик участка, диспетчер",
                f"{s} ({face}) стоит: {'отказ' if v['state'] == 'down' else 'остановлен'}. "
                + ("Диспетчер уже перераспределяет самосвалы." if ctl.get("dispatch_mode") == "B"
                   else "Самосвалы закреплены за ним и будут ждать — перевести их на другой забой."),
                EFFECT["dispatch"] if ctl.get("dispatch_mode") != "B" else "",
                None if ctl.get("dispatch_mode") == "B" else _act(CONTROL, "dispatch_mode", "B", "Включить диспетчер"))

    crusher = twins.get("crusher-01", {})
    if crusher.get("state") == "jammed" or ((crusher.get("jams") or 0) > 0 and ctl.get("oversize_mode") != "B"):
        add("oversize", "critical" if crusher.get("state") == "jammed" else "warn", "Мастер дробилки",
            f"Негабарит заклинил дробилку ({crusher.get('jams') or 1} раз за смену). "
            "Включить камеру над бункером и гидромолот: глыбы разбиваются до попадания в дробилку.",
            EFFECT["oversize"], _act(CONTROL, "oversize_mode", "B", "Камера и гидромолот")
            if ctl.get("oversize_mode") != "B" else None)

    pile, plant = twins.get("stockpile-01", {}), twins.get("plant-01", {})
    fill = pile.get("fill") or 0.0
    if crusher.get("state") == "blocked":
        add("stockpile-full", "critical", "Диспетчер, начальник фабрики",
            "Склад руды полон, дробилка стоит, рудные самосвалы ждут разгрузки. "
            "Запустить фабрику или перевести самосвалы на вскрышу.",
            "", _act(WHATIF, "plant_stop", False, "Запустить фабрику") if wi.get("plant_stop") else None)
    elif plant.get("state") == "stopped" and fill > 0.8:
        add("stockpile-filling", "warn", "Начальник фабрики",
            f"Фабрика стоит, склад заполнен на {fill:.0%}. Когда он заполнится, встанет дробилка и рудные самосвалы.",
            "", _act(WHATIF, "plant_stop", False, "Запустить фабрику") if wi.get("plant_stop") else None)
    elif plant.get("state") == "starved" or (plant.get("state") == "running" and fill < 0.1):
        level = f"{pile.get('level_t') or 0:,.0f}".replace(",", " ")
        add("plant-starved", "warn", "Диспетчер",
            f"На складе {level} т — фабрике не хватает руды. Направить больше самосвалов на рудный забой.")

    if wi.get("road_dump_closed"):
        add("road-closed", "info", "Дорожная бригада",
            "Дорога на основной отвал закрыта, вскрыша идёт на резервный: рейс длиннее. Восстановить дорогу.",
            "", _act(WHATIF, "road_dump_closed", False, "Дорога восстановлена"))
    if wi.get("rain"):
        rr = twins.get("grader-01", {}).get("rolling_resistance") or 0.04
        add("rain", "info", "Дорожная бригада",
            f"Дождь: сопротивление качению {rr:.1%} вместо 2 %, самосвалы медленнее и расходуют больше дизеля. "
            "Грейдер выведен на дороги и снижает его до 3 %; снизить скорость на спусках.")
    if (twins.get("dozer-01", {}).get("state")) == "down":
        add("dozer-down", "warn", "Механик участка, мастер отвала",
            "Бульдозер на отвале стоит: породу некому сталкивать под откос, отвал принимает по одному самосвалу "
            "и медленнее. Вывести резервный бульдозер или отремонтировать.",
            "", _act(WHATIF, "dozer_down", False, "Бульдозер в работе") if wi.get("dozer_down") else None)
    ws = twins.get("workshop-01", {})
    repair = ws.get("trucks_in_repair") or 0
    if repair >= 2:
        add("repair", "info", "Механическая служба",
            f"В мастерской самосвалов: {repair}"
            + (f", ещё {ws['queue']} ждут свободного места" if ws.get("queue") else "")
            + ". Парк работает неполным составом.")
    if ws.get("field_repairs"):
        add("breakdown", "warn", "Механик участка, диспетчер",
            "Самосвал сломался на дороге и стоит на обочине, ремонт на месте. Встречные и попутные объезжают "
            "его по одному — выслать техничку и предупредить водителей.")
    fuel = twins.get("fuel-01", {})
    if (fuel.get("queue") or 0) >= 2:
        add("fuel-queue", "info", "Заправщик",
            f"На заправке очередь {fuel['queue']} самосвала. Подогнать топливозаправщик к забою.")
    for face, name in (("face-upper", "вскрышном"), ("face-lower", "рудном")):
        if twins.get(face, {}).get("blocked_no_rock"):
            add(f"{face}-no-rock", "warn", "Начальник смены, буровзрывной участок",
                f"В {name} забое кончилась взорванная горная масса, экскаватор стоит до следующего взрыва. "
                "Ускорить взрыв или перевести экскаватор.", "",
                _act(WHATIF, "blast_now", 1, "Взорвать сейчас") if phase == "planned" else None)
    if phase == "evacuation":
        add("evacuation", "info", "Начальник смены",
            "Эвакуация перед взрывом: самосвалы выведены из забоев на стоянку, водители обедают — обед совмещён "
            "с простоем на взрыв.")
    if wi.get("calm_wind") and phase in ("planned", "evacuation", "gas") and ctl.get("blast_mode") != "B":
        add("calm", "warn", "Начальник смены",
            "Безветрие: газ после взрыва рассеивается медленно, ручной замер через 120 мин может пустить людей "
            "при превышении ПДК. Использовать дрон.",
            EFFECT["drone"], _act(CONTROL, "blast_mode", "B", "Допуск по дрону"))
    return sorted(out, key=lambda r: LEVELS[r["level"]])


class AdvisorService:
    def __init__(self, bus=None):
        self.params = Params()
        self.bus = bus or Bus("pit-advisor", [f"{EVENTS}/#"])
        self.twins: dict[str, dict] = {}
        self.last = None
        self.active: dict[str, dict] = {}
        self.history: list[dict] = []
        self.clock = 0.0
        self._actions: dict[str, dict] = {}

    def update(self, name: str, msg: dict) -> None:
        values = control_values(msg.get("path", ""), msg.get("value"))
        if values:
            self.twins.setdefault(name, {}).update(values)

    def track(self, items: list[dict]) -> None:
        """History of advice: when it appeared, when it went away and whether
        the suggested setting was applied."""
        now = float(self.twins.get("pit-01", {}).get("sim_time_s") or 0.0)
        if now < self.clock:
            self.active, self.history = {}, []
        self.clock = now
        current = {r["id"]: r for r in items}
        for rid, r in current.items():
            if rid not in self.active:
                entry = {"id": rid, "level": r["level"], "who": r["who"], "text": r["text"],
                         "since": _clock(now), "until": None, "applied": None}
                self.active[rid] = entry
                self.history.append(entry)
        for rid in [k for k in self.active if k not in current]:
            entry = self.active.pop(rid)
            entry["until"] = _clock(now)
        for entry in self.history:
            if entry["until"] is None:
                continue
            if entry["applied"] is None:
                act = self._actions.get(entry["id"])
                if act:
                    entry["applied"] = self.twins.get(act["thing"], {}).get(act["feature"]) == act["value"]
        self.history = self.history[-30:]

    def publish(self) -> None:
        items = advise(self.twins, self.params)
        for r in items:
            if r.get("action"):
                self._actions[r["id"]] = r["action"]
        self.track(items)
        text = json.dumps(items, ensure_ascii=False)
        ids = [r["id"] for r in items]
        if ids != self.last:
            log.info("recommendations: %s", ids or "none")
            self.last = ids
        self.bus.send(SERVICE, modify_features(SERVICE, {
            "count": {"value": len(items)},
            "critical": {"value": sum(r["level"] == "critical" for r in items)},
            "items": {"value": text},
            "history": {"value": json.dumps(self.history[::-1], ensure_ascii=False)},
        }))

    def run(self) -> None:
        self.bus.connect()
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        next_status = 0.0
        while not stop:
            for topic, msg in self.bus.drain():
                name = topic.rsplit("/", 1)[-1]
                if name != SERVICE:
                    self.update(name, msg)
            if time.monotonic() >= next_status:
                self.publish()
                next_status = time.monotonic() + 2.0
            time.sleep(0.02)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    AdvisorService().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
