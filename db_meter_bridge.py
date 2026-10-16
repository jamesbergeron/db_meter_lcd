#!/usr/bin/env python3
"""
db_meter_bridge.py — Web UI + alerts for db_meter_sender (Raspberry Pi / LAN)

Serves a mobile-friendly dashboard (iPhone & Android) and connects to the
local TCP stream from db_meter_sender.py. Over-threshold alerts via Discord
and/or ntfy (push to phone even when the browser is closed).

Default settings: web_config.json in this directory (editable in the Web UI).

Usage:
    python3 db_meter_bridge.py
    python3 db_meter_bridge.py --config /path/to/web_config.json

Run db_meter_sender.py on the same host first (default TCP port 55124).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(SCRIPT_DIR, "web")
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "web_config.json")

CONFIG_KEYS = (
    "tcp_host",
    "tcp_port",
    "alarm_threshold",
    "alarm_cooldown_seconds",
    "discord_enabled",
    "discord_webhook_url",
    "discord_user_id",
    "ntfy_enabled",
    "ntfy_server",
    "ntfy_topic",
    "ntfy_priority",
    "http_port",
    "http_bind",
)

DEFAULTS: dict[str, Any] = {
    "tcp_host": "127.0.0.1",
    "tcp_port": 55124,
    "alarm_threshold": 105.0,
    "alarm_cooldown_seconds": 60,
    "discord_enabled": False,
    "discord_webhook_url": "",
    "discord_user_id": "",
    "ntfy_enabled": False,
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_priority": "high",
    "http_port": 8080,
    "http_bind": "0.0.0.0",
}


def load_config(path: str) -> dict[str, Any]:
    cfg = deepcopy(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for key in CONFIG_KEYS:
                if key in raw:
                    cfg[key] = raw[key]
        except Exception as e:
            print(f"[WARN] Could not read {path}: {e}", flush=True)
    return normalize_config(cfg)


def normalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(DEFAULTS)
    for key in CONFIG_KEYS:
        if key in cfg:
            out[key] = cfg[key]
    out["tcp_port"] = int(out["tcp_port"])
    out["http_port"] = int(out["http_port"])
    out["alarm_threshold"] = float(out["alarm_threshold"])
    out["alarm_cooldown_seconds"] = max(5, int(out["alarm_cooldown_seconds"]))
    out["discord_enabled"] = bool(out["discord_enabled"])
    out["ntfy_enabled"] = bool(out["ntfy_enabled"])
    out["tcp_host"] = str(out["tcp_host"]).strip()
    out["discord_webhook_url"] = str(out["discord_webhook_url"]).strip()
    out["discord_user_id"] = str(out["discord_user_id"]).strip()
    out["ntfy_server"] = str(out["ntfy_server"]).strip().rstrip("/")
    out["ntfy_topic"] = str(out["ntfy_topic"]).strip()
    out["ntfy_priority"] = str(out["ntfy_priority"]).strip() or "high"
    out["http_bind"] = str(out["http_bind"]).strip() or "0.0.0.0"
    return out


def save_config(path: str, cfg: dict[str, Any], preserve_note: str | None = None):
    """Writes user settings to JSON; keeps optional config_note for documentation."""
    to_write = {k: cfg[k] for k in CONFIG_KEYS}
    if preserve_note:
        to_write["config_note"] = preserve_note
    elif os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if "config_note" in old:
                to_write["config_note"] = old["config_note"]
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent=4)
        f.write("\n")


def send_discord_webhook(
    webhook_url: str,
    db_value: float,
    threshold: float,
    user_id: str = "",
    is_test: bool = False,
) -> tuple[bool, str]:
    if not webhook_url or not webhook_url.startswith("http"):
        return False, "Invalid webhook URL"

    user_mention = f"<@{user_id.strip()}> " if user_id.strip() else ""
    title = "TEST DISCORD ALERT" if is_test else "HIGH SOUND PRESSURE ALARM"
    desc = (
        "Test notification from db_meter_bridge Web UI."
        if is_test
        else f"Measured noise level of **{db_value:.1f} dB** exceeded **{threshold:.1f} dB**."
    )

    payload = {
        "content": f"{user_mention}**{title}**",
        "embeds": [
            {
                "title": "Noise Level Monitor Alert",
                "description": desc,
                "color": 3447003 if is_test else 16711680,
                "fields": [
                    {"name": "Current Reading", "value": f"**{db_value:.1f} dB**", "inline": True},
                    {"name": "Threshold", "value": f"**{threshold:.1f} dB**", "inline": True},
                ],
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "footer": {"text": "db_meter_bridge · LAN Web UI"},
            }
        ],
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "db_meter_bridge/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if 200 <= resp.status < 300:
                return True, "Discord alert sent."
            return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


def send_ntfy(
    server: str,
    topic: str,
    title: str,
    message: str,
    priority: str = "high",
    tags: str = "loud_sound",
) -> tuple[bool, str]:
    if not server or not topic:
        return False, "ntfy server and topic are required"

    url = f"{server.rstrip('/')}/{topic}"
    body = message.encode("utf-8")
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
        "User-Agent": "db_meter_bridge/1.0",
    }
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            if 200 <= resp.status < 300:
                return True, "ntfy notification sent."
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


class TelemetryHub:
    """Thread-safe telemetry, stats, SSE broadcast, and alarm dispatch."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self._lock = threading.Lock()
        self.config = load_config(config_path)
        self._subscribers: list[queue.Queue] = []
        self._last_discord_alert = 0.0
        self._last_ntfy_alert = 0.0
        self.reset_stats()
        self.connected = False
        self.status = "CONNECTING"
        self.db = 0.0
        self.source_label = ""
        self.alarm_active = False
        self.last_error = ""

    def reset_stats(self):
        self.min_db: float | None = None
        self.max_db: float | None = None
        self.sum_db = 0.0
        self.sample_count = 0

    def reset_stats_and_broadcast(self):
        with self._lock:
            self.reset_stats()
            self._broadcast()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=8)
        with self._lock:
            self._subscribers.append(q)
        self._publish_snapshot(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def reload_config(self) -> dict[str, Any]:
        with self._lock:
            self.config = load_config(self.config_path)
            return deepcopy(self.config)

    def update_config(self, partial: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = deepcopy(self.config)
            for key in CONFIG_KEYS:
                if key in partial:
                    merged[key] = partial[key]
            merged = normalize_config(merged)
            save_config(self.config_path, merged)
            self.config = merged
            return deepcopy(self.config)

    def ingest(self, payload: dict[str, Any], source_label: str, transport_up: bool):
        with self._lock:
            cfg = self.config
            status = str(payload.get("status", "OK")).upper()
            self.status = status
            self.source_label = source_label
            self.connected = transport_up

            if status == "OK":
                db_val = float(payload.get("db", 0.0))
                self.db = db_val
                self.sample_count += 1
                self.sum_db += db_val
                if self.min_db is None or db_val < self.min_db:
                    self.min_db = db_val
                if self.max_db is None or db_val > self.max_db:
                    self.max_db = db_val

                threshold = cfg["alarm_threshold"]
                if db_val >= threshold:
                    self.alarm_active = True
                    self._maybe_send_alerts(db_val, threshold, cfg)
                else:
                    self.alarm_active = False
            elif not transport_up:
                self.alarm_active = False

            self._broadcast()

    def set_transport_down(self, error: str, source_label: str):
        with self._lock:
            self.connected = False
            self.status = "ERROR"
            self.last_error = error
            self.source_label = source_label
            self.alarm_active = False
            self._broadcast()

    def _maybe_send_alerts(self, db_val: float, threshold: float, cfg: dict[str, Any]):
        now = time.time()
        cooldown = cfg["alarm_cooldown_seconds"]

        if cfg["discord_enabled"] and cfg["discord_webhook_url"]:
            if now - self._last_discord_alert >= cooldown:
                ok, _ = send_discord_webhook(
                    cfg["discord_webhook_url"],
                    db_val,
                    threshold,
                    cfg["discord_user_id"],
                    is_test=False,
                )
                if ok:
                    self._last_discord_alert = now

        if cfg["ntfy_enabled"] and cfg["ntfy_topic"]:
            if now - self._last_ntfy_alert >= cooldown:
                title = f"Sound alarm: {db_val:.1f} dB"
                msg = f"Level {db_val:.1f} dB exceeded threshold {threshold:.1f} dB."
                ok, _ = send_ntfy(
                    cfg["ntfy_server"],
                    cfg["ntfy_topic"],
                    title,
                    msg,
                    priority=cfg["ntfy_priority"],
                )
                if ok:
                    self._last_ntfy_alert = now

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg = (self.sum_db / self.sample_count) if self.sample_count else None
            return {
                "db": self.db,
                "status": self.status,
                "connected": self.connected,
                "min_db": self.min_db,
                "max_db": self.max_db,
                "avg_db": avg,
                "sample_count": self.sample_count,
                "alarm_threshold": self.config["alarm_threshold"],
                "alarm_active": self.alarm_active,
                "source_label": self.source_label,
                "error": self.last_error,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }

    def _publish_snapshot(self, q: queue.Queue):
        try:
            q.put_nowait(self.snapshot())
        except queue.Full:
            pass

    def _broadcast(self):
        snap = self.snapshot()
        dead: list[queue.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(snap)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(snap)
                except Exception:
                    dead.append(q)
        for q in dead:
            if q in self._subscribers:
                self._subscribers.remove(q)


class TCPIngestThread(threading.Thread):
    """Reads newline-delimited JSON from db_meter_sender TCP server."""

    def __init__(self, hub: TelemetryHub):
        super().__init__(daemon=True, name="TCPIngest")
        self.hub = hub
        self.running = True
        self._restart = threading.Event()

    def trigger_reconnect(self):
        self._restart.set()

    def stop(self):
        self.running = False
        self._restart.set()

    def run(self):
        backoff = 1.0
        sock = None
        while self.running:
            cfg = self.hub.reload_config()
            host = cfg["tcp_host"]
            port = cfg["tcp_port"]
            label = f"{host}:{port}"

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((host, port))
                sock.settimeout(2.0)
                backoff = 1.0
                buf = b""

                while self.running and not self._restart.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("connection closed")

                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            payload = json.loads(line.decode("utf-8").strip())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        self.hub.ingest(payload, label, transport_up=True)

            except Exception as e:
                self.hub.set_transport_down(str(e), label)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                    sock = None

            if not self.running:
                break

            if self._restart.is_set():
                self._restart.clear()
                backoff = 1.0
                continue

            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)


def create_app(hub: TelemetryHub, ingest: TCPIngestThread) -> Flask:
    app = Flask(__name__, static_folder=WEB_DIR, static_url_path="/static")

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/api/config")
    def get_config():
        return jsonify(hub.reload_config())

    @app.post("/api/config")
    def post_config():
        body = request.get_json(silent=True) or {}
        try:
            updated = hub.update_config(body)
        except (TypeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400

        if not updated.get("tcp_host"):
            return jsonify({"error": "tcp_host is required"}), 400

        ingest.trigger_reconnect()
        return jsonify({"ok": True, "config": updated})

    @app.get("/api/latest")
    def latest():
        return jsonify(hub.snapshot())

    @app.post("/api/stats/reset")
    def reset_stats():
        hub.reset_stats_and_broadcast()
        return jsonify({"ok": True})

    @app.post("/api/test/discord")
    def test_discord():
        body = request.get_json(silent=True) or {}
        url = str(body.get("discord_webhook_url", "")).strip()
        uid = str(body.get("discord_user_id", "")).strip()
        threshold = float(body.get("alarm_threshold", hub.config["alarm_threshold"]))
        ok, msg = send_discord_webhook(url, 106.5, threshold, uid, is_test=True)
        return jsonify({"ok": ok, "message" if ok else "error": msg})

    @app.post("/api/test/ntfy")
    def test_ntfy():
        body = request.get_json(silent=True) or {}
        server = str(body.get("ntfy_server", "https://ntfy.sh")).strip().rstrip("/")
        topic = str(body.get("ntfy_topic", "")).strip()
        priority = str(body.get("ntfy_priority", "high")).strip() or "high"
        threshold = float(body.get("alarm_threshold", hub.config["alarm_threshold"]))
        title = "dB meter test"
        message = f"Test alert from Web UI (threshold {threshold:.1f} dB)."
        ok, msg = send_ntfy(server, topic, title, message, priority=priority, tags="test_tube")
        return jsonify({"ok": ok, "message" if ok else "error": msg})

    @app.get("/api/stream")
    def stream():
        def generate():
            q = hub.subscribe()
            try:
                while True:
                    try:
                        snap = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(snap)}\n\n"
            finally:
                hub.unsubscribe(q)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def main():
    parser = argparse.ArgumentParser(description="Web UI bridge for db_meter_sender")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to web_config.json (default: {DEFAULT_CONFIG_PATH})",
    )
    args = parser.parse_args()
    config_path = os.path.abspath(args.config)

    if not os.path.exists(config_path):
        save_config(config_path, DEFAULTS)
        print(f"[INFO] Created default config: {config_path}", flush=True)

    hub = TelemetryHub(config_path)
    ingest = TCPIngestThread(hub)
    ingest.start()

    cfg = hub.reload_config()
    app = create_app(hub, ingest)

    print("=" * 55, flush=True)
    print("  db_meter_bridge — Web UI + Discord / ntfy alerts", flush=True)
    print(f"  Config : {config_path}", flush=True)
    print(f"  Sender : TCP {cfg['tcp_host']}:{cfg['tcp_port']}", flush=True)
    print(f"  HTTP   : http://{cfg['http_bind']}:{cfg['http_port']}/", flush=True)
    print("  Open this URL on your phone (same Wi‑Fi as the Pi).", flush=True)
    print("=" * 55, flush=True)

    try:
        app.run(
            host=cfg["http_bind"],
            port=cfg["http_port"],
            threaded=True,
            use_reloader=False,
        )
    finally:
        ingest.stop()


if __name__ == "__main__":
    main()
