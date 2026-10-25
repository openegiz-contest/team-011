"""Builds neta-pit.json, the Grafana dashboard of the pit twins.

    python3 mine/grafana/build_dashboard.py
"""

import json
from pathlib import Path

DS = {"type": "influxdb", "uid": "opentwins-influxdb"}
NS = "neta.pit"


def flux(thing: str, *features: str, agg: str = "last") -> str:
    fields = " or ".join(f'r._field == "value_{f}_properties_value"' for f in features)
    return (f'from(bucket: "default")\n'
            f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
            f'  |> filter(fn: (r) => r.thingId == "{NS}:{thing}" and ({fields}))\n'
            f'  |> aggregateWindow(every: v.windowPeriod, fn: {agg}, createEmpty: false)\n'
            f'  |> keep(columns: ["_time", "_field", "_value"])')


def rename(*pairs):
    return [{"matcher": {"id": "byName", "options": f"value_{f}_properties_value"},
             "properties": [{"id": "displayName", "value": label}]}
            for f, label in pairs]


panels = []


def add(kind, title, thing, features, x, y, w, h, unit="none", decimals=0, overrides=(), thresholds=None,
        description="", agg="last"):
    defaults = {"unit": unit, "decimals": decimals}
    if kind == "stat" and not thresholds:
        defaults["color"] = {"mode": "fixed", "fixedColor": "#4ea1ff"}
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
        defaults["color"] = {"mode": "thresholds"}
    panel = {
        "id": len(panels) + 1, "type": kind, "title": title, "description": description, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "datasource": DS, "query": flux(thing, *features, agg=agg)}],
        "fieldConfig": {"defaults": defaults, "overrides": list(overrides)},
    }
    if kind == "stat":
        panel["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                            "colorMode": "value", "graphMode": "area", "textMode": "value"}
    else:
        defaults["custom"] = {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never", "spanNulls": True}
        if thresholds:
            defaults["custom"]["thresholdsStyle"] = {"mode": "line+area"}
            defaults["color"] = {"mode": "palette-classic"}
        panel["options"] = {"legend": {"displayMode": "list", "placement": "bottom"},
                            "tooltip": {"mode": "multi"}}
    panels.append(panel)


green = {"color": "green", "value": None}

add("stat", "Перевезено, т", "pit-01", ["total_t"], 0, 0, 4, 4, "masst")
add("stat", "Руда, т", "pit-01", ["ore_t"], 4, 0, 4, 4, "masst")
add("stat", "Темп, т/ч", "pit-01", ["rate_tph"], 8, 0, 4, 4)
add("stat", "Простой дробилки, мин", "crusher-01", ["down_min"], 12, 0, 4, 4,
    thresholds=[green, {"color": "orange", "value": 10}, {"color": "red", "value": 30}],
    description="Модуль 2: остановки дробилки из-за зависания негабарита")
add("stat", "Забой закрыт, мин", "pit-01", ["ore_face_shut_min"], 16, 0, 4, 4,
    description="Модуль 3: рудный забой, от начала эвакуации до допуска")
add("stat", "В газе > ПДК, мин", "pit-01", ["gas_exposure_min"], 20, 0, 4, 4, decimals=1,
    thresholds=[green, {"color": "red", "value": 0.01}],
    description="Сколько минут зона была уже открыта, а истинная концентрация ещё выше ПДК")

add("timeseries", "Накопленный тоннаж", "pit-01", ["total_t", "ore_t", "waste_t"], 0, 4, 12, 8, "masst",
    overrides=rename(("total_t", "всего"), ("ore_t", "руда"), ("waste_t", "вскрыша")))
add("timeseries", "Бункер дробилки, т (ёмкость 450 т)", "crusher-01", ["pocket_t"], 12, 4, 12, 8, "masst",
    overrides=rename(("pocket_t", "бункер")))

add("timeseries", "NO₂ в рудной и вскрышной зонах, мг/м³ (ПДК 2)", "drone-01", ["no2_lower", "no2_upper"],
    0, 12, 12, 8, decimals=2,
    thresholds=[{"color": "transparent", "value": None}, {"color": "red", "value": 2}],
    overrides=rename(("no2_lower", "руда"), ("no2_upper", "вскрыша")), agg="max",
    description="Модуль 3: показания по облётам дрона (режим Б) или ручным замерам ВГСЧ (режим А)")
add("timeseries", "CO в рудной и вскрышной зонах, мг/м³ (ПДК 20)", "drone-01", ["co_lower", "co_upper"],
    12, 12, 12, 8, decimals=1,
    thresholds=[{"color": "transparent", "value": None}, {"color": "red", "value": 20}],
    overrides=rename(("co_lower", "руда"), ("co_upper", "вскрыша")), agg="max")

add("timeseries", "Целевые потоки диспетчера, т/ч", "dispatcher-01", ["target_lower_tph", "target_upper_tph"],
    0, 20, 12, 8, overrides=rename(("target_lower_tph", "руда"), ("target_upper_tph", "вскрыша")),
    description="Модуль 1: верхний уровень (ЛП). Ноль — забой закрыт или дробилка зависла")
add("timeseries", "Негабарит: глыбы, зависания, разбито гидромолотом", "crusher-01",
    ["boulders", "jams", "rockbreaks"], 12, 20, 12, 8,
    overrides=rename(("boulders", "глыб"), ("jams", "зависаний"), ("rockbreaks", "разбито")))
add("timeseries", "Очереди самосвалов, ч (накопленно)", "pit-01", ["queue_h"], 0, 28, 12, 7, decimals=1,
    overrides=rename(("queue_h", "очереди")))
add("timeseries", "Дизель, л/т", "pit-01", ["l_per_t_ore", "l_per_t_waste"], 12, 28, 12, 7, decimals=3,
    overrides=rename(("l_per_t_ore", "руда"), ("l_per_t_waste", "вскрыша")))

dashboard = {
    "uid": "neta-pit", "title": "Карьер NETA — модули 1–3", "tags": ["neta", "pit"],
    "timezone": "browser", "refresh": "5s", "time": {"from": "now-15m", "to": "now"},
    "schemaVersion": 39, "editable": True, "panels": panels,
}
out = Path(__file__).with_name("neta-pit.json")
out.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
print(f"wrote {out} ({len(panels)} panels)")
