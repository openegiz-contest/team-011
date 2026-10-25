"""Platform access: MQTT (telemetry in, Ditto events and live messages out) and
the Ditto HTTP API for creating twins."""

import base64
import json
import logging
import os
import queue
import time
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

from .twins import NAMESPACE

log = logging.getLogger("bus")

TELEMETRY = "telemetry"
EVENTS = f"opentwins/twin/events/{NAMESPACE}"
MESSAGES = f"opentwins/live/messages/{NAMESPACE}"


class Bus:
    """MQTT client whose incoming messages are queued for the caller's thread."""

    def __init__(self, client_id: str, subscriptions: list[str]):
        self.host = os.environ.get("MQTT_HOST", "localhost")
        self.port = int(os.environ.get("MQTT_PORT", "1883"))
        self.inbox: queue.Queue = queue.Queue()
        self.subscriptions = subscriptions
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{client_id}-{os.getpid()}")
        self.client.on_connect = self._on_connect
        self.client.on_message = lambda c, u, msg: self.inbox.put((msg.topic, msg.payload))

    def _on_connect(self, client, userdata, flags, reason, properties):
        for topic in self.subscriptions:
            client.subscribe(topic, qos=1)
        log.info("connected to mqtt://%s:%s, subscribed %s", self.host, self.port, self.subscriptions)

    def connect(self) -> None:
        for _ in range(60):
            try:
                self.client.connect(self.host, self.port, keepalive=30)
                break
            except OSError as exc:
                log.warning("MQTT %s:%s not reachable (%s), retrying", self.host, self.port, exc)
                time.sleep(2)
        else:
            raise SystemExit(f"giving up on MQTT {self.host}:{self.port}")
        self.client.loop_start()

    def send(self, name: str, message: dict) -> None:
        self.client.publish(f"{TELEMETRY}/{name}", json.dumps(message), qos=0)

    def drain(self):
        while True:
            try:
                topic, payload = self.inbox.get_nowait()
            except queue.Empty:
                return
            try:
                yield topic, json.loads(payload)
            except ValueError:
                log.warning("non-JSON message on %s", topic)


class Ditto:
    def __init__(self):
        self.url = os.environ.get("DITTO_URL", "http://localhost:8080").rstrip("/")
        user = os.environ.get("DITTO_USER", "ditto")
        self.auth = "Basic " + base64.b64encode(f"{user}:{os.environ['DITTO_PASSWORD']}".encode()).decode()

    def request(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            f"{self.url}/api/2{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)

    def put_thing(self, thing_id: str, body: dict, attempts: int = 40) -> int:
        for attempt in range(attempts):
            try:
                return self.request("PUT", f"/things/{thing_id}", body)[0]
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == attempts - 1:
                    raise
                log.warning("Ditto not ready for %s (%s), retrying", thing_id, exc)
                time.sleep(3)
        return 0
