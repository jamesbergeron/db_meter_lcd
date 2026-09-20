#!/usr/bin/env python3
"""
Graphical LCD 7-Segment dB Sound Level Meter
Cross-Platform (Windows / Linux) GUI for Modbus RTU Noise Sensor
Features: Vector 7-segment display, Flashing Red Alarm (>105 dB), Bar Meter, Peak Hold, Statistics,
         Piecewise Linear Calibration Curve, Network UDP/TCP Source (Raspberry Pi sender support)
"""

import sys
import os
import time
import math
import random
import json
import socket
import urllib.request
import urllib.parse
import threading
import queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

# Try importing pyserial; if missing, serial operations are unavailable
try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


# ==========================================
# Modbus RTU CRC16 Utility
# ==========================================
def calculate_crc(data: bytes) -> bytes:
    """Calculates standard Modbus RTU CRC16."""
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for _ in range(8):
            if (crc & 0x0001) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


# ==========================================
# Discord Webhook Notification Utility
# ==========================================
def send_discord_webhook(webhook_url: str, db_value: float, threshold: float = 105.0, user_id: str = "", is_test: bool = False):
    """Sends a rich embedded notification to a Discord webhook."""
    if not webhook_url or not webhook_url.startswith("http"):
        return False, "Invalid Webhook URL"

    try:
        user_mention = f"<@{user_id.strip()}> " if user_id.strip() else ""
        title = "🧪 TEST DISCORD ALERT" if is_test else "🚨 HIGH SOUND PRESSURE ALARM ALERT!"
        desc = (
            f"Test notification from Sound Level Monitor.\nWebhook is working correctly!"
            if is_test
            else f"Measured noise level of **{db_value:.1f} dB** has exceeded the threshold of **{threshold:.1f} dB**!"
        )

        payload = {
            "content": f"{user_mention}**{title}**",
            "embeds": [
                {
                    "title": "🔊 Noise Level Monitor Alert",
                    "description": desc,
                    "color": 3447003 if is_test else 16711680,  # Blue for test, Red for alarm
                    "fields": [
                        {"name": "Current Reading", "value": f"**{db_value:.1f} dB**", "inline": True},
                        {"name": "Threshold Limit", "value": f"**{threshold:.1f} dB**", "inline": True},
                    ],
                    "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ"),
                    "footer": {"text": "Digital Sound Level Monitor • Modbus RTU"}
                }
            ]
        }

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'SoundMeter/1.0'
            }
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return True, "Alert sent successfully!"

    except Exception as e:
        return False, str(e)


# ==========================================
# Serial / Modbus Reader Worker Thread
# ==========================================
class ModbusReaderThread(threading.Thread):
    def __init__(self, data_queue, port="COM6", baudrate=9600, slave_addr=1, poll_interval=0.2):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.port = port
        self.baudrate = baudrate
        self.slave_addr = slave_addr
        self.poll_interval = poll_interval
        self.running = True
        self.ser = None
        self._lock = threading.Lock()
        self._wake_event = threading.Event()

    def set_config(self, port, baudrate, slave_addr=1):
        with self._lock:
            self.port = port
            self.baudrate = baudrate
            self.slave_addr = slave_addr
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
        # Drain any leftover messages from queue
        try:
            while not self.data_queue.empty():
                self.data_queue.get_nowait()
        except Exception:
            pass
        self._wake_event.set()

    def stop(self):
        self.running = False
        self._wake_event.set()

    def run(self):
        while self.running:
            self._wake_event.clear()
            with self._lock:
                port = self.port
                baudrate = self.baudrate
                slave_addr = self.slave_addr

            if not HAS_SERIAL:
                self.data_queue.put({
                    'status': 'ERROR',
                    'error': 'pyserial module is missing! Run: pip install pyserial',
                    'port': port,
                    'timestamp': datetime.now().strftime("%H:%M:%S")
                })
                self._wake_event.wait(1.0)
                continue

            # Real Serial Modbus RTU Read
            try:
                # Close serial port if port or baudrate changed
                if self.ser is not None and (self.ser.port != port or self.ser.baudrate != baudrate):
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None

                if self.ser is None or not self.ser.is_open:
                    self.ser = serial.Serial(
                        port=port,
                        baudrate=baudrate,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=0.2
                    )

                # Query Frame: [SlaveID, Func(0x03), RegHi, RegLo, CountHi, CountLo]
                query_payload = bytes([slave_addr, 0x03, 0x00, 0x00, 0x00, 0x01])
                query_frame = calculate_crc(query_payload)

                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                self.ser.write(query_frame)
                self.ser.flush()

                time.sleep(0.06)
                response = self.ser.read(7)
                timestamp = datetime.now().strftime("%H:%M:%S")

                if len(response) >= 5 and response[1] == 0x03:
                    raw_val = (response[3] << 8) | response[4]
                    db_val = raw_val / 10.0
                    self.data_queue.put({
                        'status': 'OK',
                        'db': db_val,
                        'port': port,
                        'timestamp': timestamp
                    })
                else:
                    self.data_queue.put({
                        'status': 'TIMEOUT',
                        'error': f'No Modbus response from {port} (Baud: {baudrate}, Slave: {slave_addr})',
                        'port': port,
                        'timestamp': timestamp
                    })

            except Exception as e:
                self.data_queue.put({
                    'status': 'ERROR',
                    'error': f'Serial error on {port}: {e}',
                    'port': port,
                    'timestamp': datetime.now().strftime("%H:%M:%S")
                })
                if self.ser:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                self._wake_event.wait(1.0)

            self._wake_event.wait(self.poll_interval)

        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass


# ==========================================
# UDP Broadcast Listener Thread
# ==========================================
class UDPListenerThread(threading.Thread):
    """
    Listens for UDP broadcast packets sent by db_meter_sender.py.
    Parses JSON payloads and puts messages onto the shared data_queue
    in the same format as ModbusReaderThread.
    """
    def __init__(self, data_queue, udp_port=55123, sender_filter=""):
        super().__init__(daemon=True, name="UDPListener")
        self.data_queue = data_queue
        self.udp_port = udp_port
        self.sender_filter = sender_filter.strip()
        self.running = True
        self._sock = None

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(1.0)
            self._sock.bind(("0.0.0.0", self.udp_port))
        except OSError as e:
            self.data_queue.put({
                'status': 'ERROR',
                'db': 0.0,
                'error': f'UDP bind failed on port {self.udp_port}: {e}',
                'port': f'UDP:{self.udp_port}',
                'source': 'udp',
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            return

        while self.running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                payload = json.loads(data.decode("utf-8").strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            # Optional sender filter by IP or sender name
            if self.sender_filter:
                sender_name = payload.get("sender", "")
                if self.sender_filter not in addr[0] and self.sender_filter != sender_name:
                    continue

            status = payload.get("status", "OK")
            db_val = float(payload.get("db", 0.0))
            sender_id = f"{addr[0]} ({payload.get('sender', 'unknown')})"

            self.data_queue.put({
                'status': status,
                'db': db_val,
                'error': payload.get("error", ""),
                'port': sender_id,
                'source': 'udp',
                'timestamp': payload.get("timestamp", datetime.now().strftime("%H:%M:%S"))
            })

        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


# ==========================================
# TCP Client Thread (connects to Pi sender)
# ==========================================
class TCPClientThread(threading.Thread):
    """
    Connects to a db_meter_sender.py TCP server running on the Raspberry Pi.
    Reads newline-delimited JSON and puts messages onto data_queue.
    Automatically reconnects on disconnect with backoff.
    """
    def __init__(self, data_queue, host, tcp_port=55124):
        super().__init__(daemon=True, name="TCPClient")
        self.data_queue = data_queue
        self.host = host
        self.tcp_port = tcp_port
        self.running = True
        self._sock = None

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def run(self):
        backoff = 1.0
        while self.running:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(5.0)
                self._sock.connect((self.host, self.tcp_port))
                self._sock.settimeout(2.0)
                backoff = 1.0  # reset on successful connect

                self.data_queue.put({
                    'status': 'OK',
                    'db': 0.0,
                    'port': f'{self.host}:{self.tcp_port}',
                    'source': 'tcp',
                    'timestamp': datetime.now().strftime("%H:%M:%S")
                })

                buf = b""
                while self.running:
                    try:
                        chunk = self._sock.recv(4096)
                    except socket.timeout:
                        continue
                    except (OSError, ConnectionResetError):
                        break

                    if not chunk:
                        break

                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            payload = json.loads(line.decode("utf-8").strip())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue

                        status = payload.get("status", "OK")
                        db_val = float(payload.get("db", 0.0))
                        sender_id = f"{self.host}:{self.tcp_port}"

                        self.data_queue.put({
                            'status': status,
                            'db': db_val,
                            'error': payload.get("error", ""),
                            'port': sender_id,
                            'source': 'tcp',
                            'timestamp': payload.get("timestamp", datetime.now().strftime("%H:%M:%S"))
                        })

            except (OSError, ConnectionRefusedError, socket.timeout) as e:
                self.data_queue.put({
                    'status': 'ERROR',
                    'db': 0.0,
                    'error': f'TCP: Cannot connect to {self.host}:{self.tcp_port} — {e}',
                    'port': f'{self.host}:{self.tcp_port}',
                    'source': 'tcp',
                    'timestamp': datetime.now().strftime("%H:%M:%S")
                })
            finally:
                if self._sock:
                    try:
                        self._sock.close()
                    except Exception:
                        pass
                    self._sock = None

            # Wait before reconnecting
            for _ in range(int(backoff * 10)):
                if not self.running:
                    break
                time.sleep(0.1)
            backoff = min(backoff * 2, 15.0)  # cap at 15s


# ==========================================
# Color Themes Configuration
# ==========================================
THEMES = {
    'Matrix Green': {
        'bg': '#0a120b',
        'panel_bg': '#0f1f12',
        'bezel_border': '#1d3b23',
        'on_color': '#00FF66',
        'off_color': '#112918',
        'text': '#70FF9C',
        'subtext': '#3C8C56',
        'alarm_bg': '#4a0000',
        'alarm_flash': '#cc0000',
        'alarm_on': '#FFFFFF',
    },
    'Retro Amber': {
        'bg': '#120c02',
        'panel_bg': '#211604',
        'bezel_border': '#422c08',
        'on_color': '#FFB000',
        'off_color': '#2e1f02',
        'text': '#FFCF66',
        'subtext': '#8C631A',
        'alarm_bg': '#4a0000',
        'alarm_flash': '#cc0000',
        'alarm_on': '#FFFFFF',
    },
    'Cyber Cyan': {
        'bg': '#041017',
        'panel_bg': '#071b26',
        'bezel_border': '#0e384f',
        'on_color': '#00E5FF',
        'off_color': '#082c3d',
        'text': '#80F2FF',
        'subtext': '#2B7A8C',
        'alarm_bg': '#4a0000',
        'alarm_flash': '#cc0000',
        'alarm_on': '#FFFFFF',
    },
    'Crimson Red': {
        'bg': '#140404',
        'panel_bg': '#240808',
        'bezel_border': '#451010',
        'on_color': '#FF2A2A',
        'off_color': '#330d0d',
        'text': '#FF8080',
        'subtext': '#8C2B2B',
        'alarm_bg': '#4a0000',
        'alarm_flash': '#cc0000',
        'alarm_on': '#FFFFFF',
    }
}


# ==========================================
# Main GUI Application
# ==========================================
class SoundMeterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Digital Sound Level Meter (LCD Modbus RTU)")
        self.root.geometry("920x680")
        self.root.minsize(750, 550)

        # Application state defaults
        self.theme_name = 'Matrix Green'
        self.alarm_threshold = 105.0
        self.alarm_active = False
        self.flash_state = False

        self.current_db = 0.0
        self.min_db = 999.9
        self.max_db = 0.0
        self.sample_count = 0
        self.sum_db = 0.0
        self.peak_hold_db = 30.0
        self.peak_hold_timer = 0

        # Discord Webhook State defaults
        self.discord_enabled = False
        self.discord_webhook_url = ""
        self.discord_user_id = ""
        self.discord_cooldown = 60  # seconds
        self.last_discord_alert_time = 0.0

        # Calibration Curve defaults
        # List of [raw_db, corrected_db] pairs, sorted by raw_db
        self.calibration_points = []
        self.calibration_enabled = False

        # Network source defaults
        self.source_mode = 'serial'   # 'serial' | 'udp' | 'tcp'
        self.udp_listen_port = 55123
        self.tcp_host = ''
        self.tcp_remote_port = 55124
        self.net_sender_filter = ''
        self._net_thread = None       # Active network thread (UDPListenerThread or TCPClientThread)

        # Load persistent configuration from config.json
        self.load_config()

        # Queue & Thread
        self.data_queue = queue.Queue()
        self.reader_thread = ModbusReaderThread(
            data_queue=self.data_queue,
            port=self.saved_port,
            baudrate=self.saved_baud,
            slave_addr=self.saved_slave
        )

        self.setup_ui()
        self.apply_theme()

        # Start the appropriate source thread
        if self.source_mode == 'serial':
            self.reader_thread.start()
        else:
            self.reader_thread.start()   # Start in background (paused at serial open)
            self.set_source_mode(self.source_mode, startup=True)

        self.root.after(50, self.process_queue)
        self.root.after(200, self.update_flash_cycle)

    def load_config(self):
        """Loads persistent application configuration from config.json."""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.saved_port = cfg.get("port", "COM6" if sys.platform.startswith('win') else "/dev/ttyACM0")
                self.saved_baud = int(cfg.get("baudrate", 9600))
                self.saved_slave = int(cfg.get("slave_addr", 1))
                self.theme_name = cfg.get("theme", "Matrix Green")
                self.alarm_threshold = float(cfg.get("alarm_threshold", 105.0))
                self.discord_enabled = bool(cfg.get("discord_enabled", False))
                self.discord_webhook_url = str(cfg.get("discord_webhook_url", ""))
                self.discord_user_id = str(cfg.get("discord_user_id", ""))
                self.discord_cooldown = int(cfg.get("discord_cooldown", 60))
                raw_pts = cfg.get("calibration_points", [])
                self.calibration_points = [[float(p[0]), float(p[1])] for p in raw_pts if len(p) == 2]
                self.calibration_enabled = bool(cfg.get("calibration_enabled", False))
                self.source_mode = cfg.get("source_mode", "serial")
                self.udp_listen_port = int(cfg.get("udp_listen_port", 55123))
                self.tcp_host = str(cfg.get("tcp_host", ""))
                self.tcp_remote_port = int(cfg.get("tcp_remote_port", 55124))
                self.net_sender_filter = str(cfg.get("net_sender_filter", ""))
                return
            except Exception as e:
                print(f"Error loading config.json: {e}")

        self.saved_port = "COM6" if sys.platform.startswith('win') else "/dev/ttyACM0"
        self.saved_baud = 9600
        self.saved_slave = 1

    def save_config(self):
        """Saves persistent application configuration to config.json."""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        cfg = {
            "port": getattr(self, "cbo_port", None) and self.cbo_port.get().strip() or getattr(self, "saved_port", "COM6"),
            "baudrate": getattr(self, "cbo_baud", None) and int(self.cbo_baud.get()) or getattr(self, "saved_baud", 9600),
            "slave_addr": getattr(self, "cbo_slave", None) and int(self.cbo_slave.get()) or getattr(self, "saved_slave", 1),
            "theme": self.theme_name,
            "alarm_threshold": self.alarm_threshold,
            "discord_enabled": self.discord_enabled,
            "discord_webhook_url": self.discord_webhook_url,
            "discord_user_id": self.discord_user_id,
            "discord_cooldown": self.discord_cooldown,
            "calibration_points": self.calibration_points,
            "calibration_enabled": self.calibration_enabled,
            "source_mode": self.source_mode,
            "udp_listen_port": self.udp_listen_port,
            "tcp_host": self.tcp_host,
            "tcp_remote_port": self.tcp_remote_port,
            "net_sender_filter": self.net_sender_filter,
        }
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
        except Exception as e:
            print(f"Error saving config.json: {e}")

    # ==========================================
    # Calibration Curve Engine
    # ==========================================
    def apply_calibration(self, raw_db: float) -> float:
        """
        Applies piecewise linear interpolation using the user-defined
        calibration curve (list of [raw, corrected] points).
        Points outside the range are extrapolated from the nearest segment.
        """
        pts = sorted(self.calibration_points, key=lambda p: p[0])
        if len(pts) < 2:
            return raw_db  # Not enough points — pass through unchanged

        # Below the lowest calibration point: extrapolate from first segment
        if raw_db <= pts[0][0]:
            x0, y0 = pts[0]
            x1, y1 = pts[1]
            slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 1.0
            return y0 + slope * (raw_db - x0)

        # Above the highest calibration point: extrapolate from last segment
        if raw_db >= pts[-1][0]:
            x0, y0 = pts[-2]
            x1, y1 = pts[-1]
            slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 1.0
            return y1 + slope * (raw_db - x1)

        # Interpolate between bracketing points
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if x0 <= raw_db <= x1:
                t = (raw_db - x0) / (x1 - x0) if x1 != x0 else 0.0
                return y0 + t * (y1 - y0)

        return raw_db  # Fallback — should not reach here

    def detect_default_port(self):
        """Finds default serial port for Windows or Linux."""
        if HAS_SERIAL:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            if ports:
                return ports[0]
        if sys.platform.startswith('win'):
            return "COM3"
        else:
            return "/dev/ttyACM0"

    def get_port_list(self):
        """Returns comprehensive list of detected and standard serial ports."""
        ports = []
        if HAS_SERIAL:
            try:
                detected = [p.device for p in serial.tools.list_ports.comports()]
                ports.extend(detected)
            except Exception:
                pass

        if sys.platform.startswith('win'):
            defaults = [f"COM{i}" for i in range(1, 21)]
        else:
            defaults = [f"/dev/ttyACM{i}" for i in range(4)] + [f"/dev/ttyUSB{i}" for i in range(4)]

        for d in defaults:
            if d not in ports:
                ports.append(d)
        return ports

    def refresh_port_list(self):
        """Refreshes available ports in combobox dropdown."""
        current = self.cbo_port.get()
        new_values = self.get_port_list()
        if current and current not in new_values:
            new_values = [current] + list(new_values)
        self.cbo_port['values'] = new_values

    def setup_ui(self):
        # Master container
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------
        # Header / Title Bar
        # ----------------------------------
        self.header_frame = tk.Frame(self.main_frame)
        self.header_frame.pack(fill=tk.X, padx=15, pady=5)

        self.lbl_title = tk.Label(
            self.header_frame,
            text="DECIBEL SOUND LEVEL MONITOR",
            font=("Segoe UI", 16, "bold")
        )
        self.lbl_title.pack(side=tk.LEFT)

        # Status Badges
        self.status_container = tk.Frame(self.header_frame)
        self.status_container.pack(side=tk.RIGHT)

        self.lbl_comm_led = tk.Label(
            self.status_container,
            text="● COMM",
            font=("Segoe UI", 10, "bold"),
            fg="#555555"
        )
        self.lbl_comm_led.pack(side=tk.LEFT, padx=8)

        self.lbl_mode_badge = tk.Label(
            self.status_container,
            text="CONNECTING...",
            font=("Segoe UI", 9, "bold"),
            bg="#f0ad4e",
            fg="#ffffff",
            bd=1,
            relief=tk.SOLID,
            padx=6, pady=2
        )
        self.lbl_mode_badge.pack(side=tk.LEFT, padx=8)

        self.lbl_alarm_badge = tk.Label(
            self.status_container,
            text="ALARM: >105 dB",
            font=("Segoe UI", 9, "bold"),
            bg="#222222",
            fg="#888888",
            padx=6, pady=2
        )
        self.lbl_alarm_badge.pack(side=tk.LEFT, padx=8)

        # ----------------------------------
        # Center LCD Instrument Panel
        # ----------------------------------
        self.lcd_outer_frame = tk.Frame(self.main_frame, bd=4, relief=tk.RIDGE)
        self.lcd_outer_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        self.canvas = tk.Canvas(self.lcd_outer_frame, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw_lcd())

        # Warning Flash Banner Overlay (initially hidden)
        self.alarm_banner = tk.Label(
            self.lcd_outer_frame,
            text="🚨 HIGH SOUND PRESSURE ALARM - LEVEL EXCEEDS 105 dB 🚨",
            font=("Segoe UI", 13, "bold"),
            bg="#FF0000",
            fg="#FFFFFF",
            pady=6
        )

        # ----------------------------------
        # Telemetry / Statistics Readouts
        # ----------------------------------
        self.stats_frame = tk.Frame(self.main_frame)
        self.stats_frame.pack(fill=tk.X, padx=15, pady=5)

        stat_cols = [
            ("MIN LEVEL", "val_min", "0.0 dB"),
            ("AVERAGE", "val_avg", "0.0 dB"),
            ("MAX LEVEL", "val_max", "0.0 dB"),
            ("CURRENT", "val_cur", "0.0 dB"),
        ]

        self.stat_labels = {}
        for col_name, var_key, default_txt in stat_cols:
            card = tk.Frame(self.stats_frame, bd=1, relief=tk.SOLID)
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

            lbl_title = tk.Label(card, text=col_name, font=("Segoe UI", 8, "bold"), fg="#777777")
            lbl_title.pack()

            lbl_val = tk.Label(card, text=default_txt, font=("Segoe UI", 13, "bold"))
            lbl_val.pack()
            self.stat_labels[var_key] = lbl_val

        # Reset Stats Button
        self.btn_reset_stats = tk.Button(
            self.stats_frame,
            text="Reset Stats",
            font=("Segoe UI", 9, "bold"),
            command=self.reset_statistics,
            relief=tk.GROOVE,
            padx=8
        )
        self.btn_reset_stats.pack(side=tk.RIGHT, fill=tk.Y, padx=4)

        # ----------------------------------
        # Bottom Control Panel
        # ----------------------------------
        self.control_frame = tk.Frame(self.main_frame)
        self.control_frame.pack(fill=tk.X, padx=15, pady=5)

        # --- Source Mode Radio Buttons ---
        self._var_source = tk.StringVar(value=self.source_mode)
        rb_serial = tk.Radiobutton(
            self.control_frame, text="◉ COM Port",
            variable=self._var_source, value="serial",
            font=("Segoe UI", 9, "bold"),
            command=self._on_source_radio_change
        )
        rb_serial.pack(side=tk.LEFT, padx=(5, 2))

        rb_net = tk.Radiobutton(
            self.control_frame, text="◎ Network",
            variable=self._var_source, value="network",
            font=("Segoe UI", 9, "bold"),
            command=self._on_source_radio_change
        )
        rb_net.pack(side=tk.LEFT, padx=(0, 10))

        # Separator
        ttk.Separator(self.control_frame, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=3)

        # --- Serial Controls Group (shown only in COM mode) ---
        self._serial_widgets = []  # track for show/hide

        lbl_port = tk.Label(self.control_frame, text="Port:", font=("Segoe UI", 9, "bold"))
        lbl_port.pack(side=tk.LEFT, padx=(5, 2))
        self._serial_widgets.append(lbl_port)

        self.cbo_port = ttk.Combobox(self.control_frame, values=self.get_port_list(), width=13, postcommand=self.refresh_port_list)
        self.cbo_port.set(self.saved_port)
        self.cbo_port.pack(side=tk.LEFT, padx=(0, 10))
        self.cbo_port.bind("<<ComboboxSelected>>", lambda e: self.apply_serial_settings())
        self._serial_widgets.append(self.cbo_port)

        lbl_baud = tk.Label(self.control_frame, text="Baud:", font=("Segoe UI", 9, "bold"))
        lbl_baud.pack(side=tk.LEFT, padx=(5, 2))
        self._serial_widgets.append(lbl_baud)

        self.cbo_baud = ttk.Combobox(self.control_frame, values=["2400", "4800", "9600", "19200", "38400", "115200"], width=7)
        self.cbo_baud.set(str(self.saved_baud))
        self.cbo_baud.pack(side=tk.LEFT, padx=(0, 10))
        self.cbo_baud.bind("<<ComboboxSelected>>", lambda e: self.apply_serial_settings())
        self._serial_widgets.append(self.cbo_baud)

        lbl_slave = tk.Label(self.control_frame, text="Slave ID:", font=("Segoe UI", 9, "bold"))
        lbl_slave.pack(side=tk.LEFT, padx=(5, 2))
        self._serial_widgets.append(lbl_slave)

        self.cbo_slave = ttk.Combobox(self.control_frame, values=["1", "2", "3", "4", "5", "6", "7", "8"], width=4)
        self.cbo_slave.set(str(self.saved_slave))
        self.cbo_slave.pack(side=tk.LEFT, padx=(0, 10))
        self.cbo_slave.bind("<<ComboboxSelected>>", lambda e: self.apply_serial_settings())
        self._serial_widgets.append(self.cbo_slave)

        # Apply serial settings button
        self.btn_apply = tk.Button(
            self.control_frame,
            text="Apply Serial",
            font=("Segoe UI", 9, "bold"),
            command=self.apply_serial_settings
        )
        self.btn_apply.pack(side=tk.LEFT, padx=5)
        self._serial_widgets.append(self.btn_apply)

        # --- Network Controls Group (shown only in Network mode) ---
        self._net_widgets = []  # track for show/hide

        self._lbl_net_mode = tk.Label(self.control_frame, text="Mode:", font=("Segoe UI", 9, "bold"))
        self._lbl_net_mode.pack(side=tk.LEFT, padx=(5, 2))
        self._net_widgets.append(self._lbl_net_mode)

        self._cbo_net_mode = ttk.Combobox(
            self.control_frame,
            values=["UDP Broadcast", "TCP Direct"],
            width=14,
            state="readonly"
        )
        self._cbo_net_mode.set("TCP Direct" if self.source_mode == "tcp" else "UDP Broadcast")
        self._cbo_net_mode.pack(side=tk.LEFT, padx=(0, 6))
        self._net_widgets.append(self._cbo_net_mode)

        self._btn_net_settings = tk.Button(
            self.control_frame,
            text="⚙ Network Settings",
            font=("Segoe UI", 9, "bold"),
            bg="#17a2b8",
            fg="#ffffff",
            activebackground="#138496",
            activeforeground="#ffffff",
            command=self.open_network_settings_dialog
        )
        self._btn_net_settings.pack(side=tk.LEFT, padx=5)
        self._net_widgets.append(self._btn_net_settings)

        # --- Shared Right-side Buttons ---
        ttk.Separator(self.control_frame, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=3)

        # Discord Alerts Button
        self.btn_discord = tk.Button(
            self.control_frame,
            text="💬 Discord Alerts",
            font=("Segoe UI", 9, "bold"),
            bg="#5865F2",
            fg="#ffffff",
            activebackground="#4752C4",
            activeforeground="#ffffff",
            command=self.open_discord_settings_dialog
        )
        self.btn_discord.pack(side=tk.LEFT, padx=5)

        # Calibration Curve Button
        self.btn_cal = tk.Button(
            self.control_frame,
            text="📐 Calibration",
            font=("Segoe UI", 9, "bold"),
            bg="#e67e22",
            fg="#ffffff",
            activebackground="#ca6f1e",
            activeforeground="#ffffff",
            command=self.open_calibration_dialog
        )
        self.btn_cal.pack(side=tk.LEFT, padx=5)

        # Theme Selector
        tk.Label(self.control_frame, text="Theme:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(10, 2))
        self.cbo_theme = ttk.Combobox(self.control_frame, values=list(THEMES.keys()), width=12, state="readonly")
        self.cbo_theme.set(self.theme_name)
        self.cbo_theme.pack(side=tk.LEFT, padx=(0, 5))
        self.cbo_theme.bind("<<ComboboxSelected>>", self.on_theme_change)

        # Apply the correct widget visibility for saved source mode
        self._apply_source_widget_visibility()

    def _on_source_radio_change(self):
        """Called when user clicks COM Port or Network radio button."""
        sel = self._var_source.get()
        if sel == "network":
            net_mode = self._cbo_net_mode.get()
            mode = "tcp" if net_mode == "TCP Direct" else "udp"
        else:
            mode = "serial"
        self.set_source_mode(mode)
        self._apply_source_widget_visibility()

    def _apply_source_widget_visibility(self):
        """Shows/hides serial vs network controls based on current source mode."""
        is_serial = (self.source_mode == 'serial')
        for w in self._serial_widgets:
            if is_serial:
                w.pack_info()  # already packed, just ensure visible
            else:
                try:
                    w.pack_forget()
                except Exception:
                    pass
        for w in self._net_widgets:
            if not is_serial:
                pass  # already visible
            else:
                try:
                    w.pack_forget()
                except Exception:
                    pass

        # Re-pack in correct order based on mode
        # We do a full re-pack of the variable section since pack ordering matters
        # First remove all tracked widgets
        for w in self._serial_widgets + self._net_widgets:
            try:
                w.pack_forget()
            except Exception:
                pass

        if is_serial:
            # Re-pack serial controls after the separtor
            sep_index = 0
            children = self.control_frame.pack_slaves()
            # Pack serials in order
            for w in self._serial_widgets:
                w.pack(side=tk.LEFT, padx=w._pack_padx if hasattr(w, '_pack_padx') else 3)
        else:
            for w in self._net_widgets:
                w.pack(side=tk.LEFT, padx=3)

    def set_source_mode(self, mode: str, startup: bool = False):
        """
        Switches the active data source.
        mode: 'serial' | 'udp' | 'tcp'
        """
        # Stop existing network thread
        if self._net_thread and self._net_thread.is_alive():
            self._net_thread.stop()
            self._net_thread = None

        # Drain queue
        try:
            while not self.data_queue.empty():
                self.data_queue.get_nowait()
        except Exception:
            pass

        self.source_mode = mode
        self._var_source.set("serial" if mode == "serial" else "network")

        if mode == 'serial':
            # Serial thread is always running; just let it take over
            self.lbl_mode_badge.config(text="CONNECTING...", bg="#f0ad4e", fg="#ffffff")

        elif mode == 'udp':
            self._net_thread = UDPListenerThread(
                data_queue=self.data_queue,
                udp_port=self.udp_listen_port,
                sender_filter=self.net_sender_filter
            )
            self._net_thread.start()
            self.lbl_mode_badge.config(
                text=f"UDP :{self.udp_listen_port}",
                bg="#17a2b8", fg="#ffffff"
            )

        elif mode == 'tcp':
            if not self.tcp_host:
                messagebox.showwarning(
                    "TCP Host Required",
                    "Please enter the Raspberry Pi IP address in Network Settings first."
                )
                self.source_mode = 'serial' if startup else self.source_mode
                self._var_source.set("serial")
                return
            self._net_thread = TCPClientThread(
                data_queue=self.data_queue,
                host=self.tcp_host,
                tcp_port=self.tcp_remote_port
            )
            self._net_thread.start()
            self.lbl_mode_badge.config(
                text=f"TCP {self.tcp_host}:{self.tcp_remote_port}",
                bg="#17a2b8", fg="#ffffff"
            )

        self.save_config()

    def open_network_settings_dialog(self):
        """Opens network configuration dialog."""
        dlg = tk.Toplevel(self.root)
        dlg.title("⚙ Network Source Settings")
        dlg.geometry("500x340")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="⚙ Network Source Configuration", font=("Segoe UI", 13, "bold")).pack(pady=(14, 4))
        tk.Label(
            dlg,
            text="Configure how this application receives data from a remote db_meter_sender.py instance.",
            font=("Segoe UI", 9), fg="#555555", wraplength=460
        ).pack(pady=(0, 10))

        f = tk.Frame(dlg, padx=28)
        f.pack(fill=tk.BOTH, expand=True)

        # Mode selector
        tk.Label(f, text="Mode:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", pady=6)
        var_mode = tk.StringVar(value="TCP Direct" if self.source_mode == "tcp" else "UDP Broadcast")
        cbo_mode = ttk.Combobox(f, textvariable=var_mode, values=["UDP Broadcast", "TCP Direct"],
                                 state="readonly", width=18)
        cbo_mode.grid(row=0, column=1, sticky="w", pady=6)

        # UDP port
        tk.Label(f, text="UDP Port:", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w", pady=6)
        ent_udp = ttk.Entry(f, width=10)
        ent_udp.insert(0, str(self.udp_listen_port))
        ent_udp.grid(row=1, column=1, sticky="w", pady=6)
        tk.Label(f, text="(matches sender's --udp-port, default 55123)",
                 font=("Segoe UI", 8), fg="#777777").grid(row=1, column=2, sticky="w", padx=8)

        # TCP host
        tk.Label(f, text="TCP Host (Pi IP):", font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w", pady=6)
        ent_host = ttk.Entry(f, width=18)
        ent_host.insert(0, self.tcp_host)
        ent_host.grid(row=2, column=1, sticky="w", pady=6)
        tk.Label(f, text="(e.g. 192.168.1.50)",
                 font=("Segoe UI", 8), fg="#777777").grid(row=2, column=2, sticky="w", padx=8)

        # TCP port
        tk.Label(f, text="TCP Port:", font=("Segoe UI", 9, "bold")).grid(row=3, column=0, sticky="w", pady=6)
        ent_tcp_port = ttk.Entry(f, width=10)
        ent_tcp_port.insert(0, str(self.tcp_remote_port))
        ent_tcp_port.grid(row=3, column=1, sticky="w", pady=6)
        tk.Label(f, text="(matches sender's --tcp-port, default 55124)",
                 font=("Segoe UI", 8), fg="#777777").grid(row=3, column=2, sticky="w", padx=8)

        # Sender filter
        tk.Label(f, text="Sender Filter (Optional):", font=("Segoe UI", 9, "bold")).grid(row=4, column=0, sticky="w", pady=6)
        ent_filter = ttk.Entry(f, width=18)
        ent_filter.insert(0, self.net_sender_filter)
        ent_filter.grid(row=4, column=1, sticky="w", pady=6)
        tk.Label(f, text="(IP or sender name — leave blank to accept any)",
                 font=("Segoe UI", 8), fg="#777777").grid(row=4, column=2, sticky="w", padx=8)

        status_lbl = tk.Label(dlg, text="", font=("Segoe UI", 9, "bold"), fg="#c0392b")
        status_lbl.pack(pady=(4, 0))

        def save_and_connect():
            try:
                self.udp_listen_port = int(ent_udp.get().strip())
            except ValueError:
                status_lbl.config(text="⚠ UDP Port must be a number (e.g. 55123)")
                return
            try:
                self.tcp_remote_port = int(ent_tcp_port.get().strip())
            except ValueError:
                status_lbl.config(text="⚠ TCP Port must be a number (e.g. 55124)")
                return

            self.tcp_host = ent_host.get().strip()
            self.net_sender_filter = ent_filter.get().strip()

            sel_mode = var_mode.get()
            new_mode = "tcp" if sel_mode == "TCP Direct" else "udp"

            if new_mode == "tcp" and not self.tcp_host:
                status_lbl.config(text="⚠ TCP Direct requires a Host IP address.")
                return

            self._cbo_net_mode.set(sel_mode)
            self._var_source.set("network")
            self.set_source_mode(new_mode)
            dlg.destroy()

        btn_box = tk.Frame(dlg, pady=8)
        btn_box.pack(fill=tk.X, padx=28)
        tk.Button(btn_box, text="Save & Connect", font=("Segoe UI", 9, "bold"),
                  bg="#17a2b8", fg="#ffffff", command=save_and_connect).pack(side=tk.RIGHT, padx=6)
        tk.Button(btn_box, text="Cancel", font=("Segoe UI", 9, "bold"),
                  command=dlg.destroy).pack(side=tk.RIGHT, padx=6)

    def open_discord_settings_dialog(self):
        """Opens popup window to configure Discord Webhook alerts."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Discord Webhook Alerts Setup")
        dlg.geometry("540x360")
        dlg.resizable(False, False)
        dlg.grab_set()  # Modal window

        tk.Label(dlg, text="💬 Discord Webhook Alerts Configuration", font=("Segoe UI", 12, "bold")).pack(pady=(15, 5))

        f = tk.Frame(dlg, padx=20, pady=10)
        f.pack(fill=tk.BOTH, expand=True)

        var_disc_enable = tk.BooleanVar(value=self.discord_enabled)
        chk = tk.Checkbutton(f, text="Enable Discord Notifications when Level > 105 dB", variable=var_disc_enable, font=("Segoe UI", 10, "bold"))
        chk.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        tk.Label(f, text="Webhook URL:", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w", pady=5)
        ent_url = ttk.Entry(f, width=42)
        ent_url.insert(0, self.discord_webhook_url)
        ent_url.grid(row=1, column=1, sticky="w", pady=5)

        tk.Label(f, text="User ID to Ping (Optional):", font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w", pady=5)
        ent_uid = ttk.Entry(f, width=22)
        ent_uid.insert(0, self.discord_user_id)
        ent_uid.grid(row=2, column=1, sticky="w", pady=5)
        tk.Label(f, text="(Right-click Discord profile -> Copy User ID)", font=("Segoe UI", 8), fg="#777777").grid(row=3, column=1, sticky="w", pady=(0, 5))

        tk.Label(f, text="Cooldown (Seconds):", font=("Segoe UI", 9, "bold")).grid(row=4, column=0, sticky="w", pady=5)
        ent_cool = ttk.Entry(f, width=10)
        ent_cool.insert(0, str(self.discord_cooldown))
        ent_cool.grid(row=4, column=1, sticky="w", pady=5)

        lbl_status = tk.Label(dlg, text="", font=("Segoe UI", 9, "bold"), fg="#5cb85c")
        lbl_status.pack(pady=2)

        def test_webhook():
            url = ent_url.get().strip()
            uid = ent_uid.get().strip()
            if not url:
                messagebox.showerror("Error", "Please enter a valid Discord Webhook URL first!")
                return
            lbl_status.config(text="Sending test alert to Discord...", fg="#f0ad4e")
            dlg.update()

            def run_test():
                ok, res = send_discord_webhook(url, db_value=106.5, threshold=self.alarm_threshold, user_id=uid, is_test=True)
                if ok:
                    lbl_status.config(text="✅ Test notification sent successfully to Discord!", fg="#5cb85c")
                else:
                    lbl_status.config(text=f"❌ Failed: {res}", fg="#d9534f")

            threading.Thread(target=run_test, daemon=True).start()

        def save_and_close():
            self.discord_enabled = var_disc_enable.get()
            self.discord_webhook_url = ent_url.get().strip()
            self.discord_user_id = ent_uid.get().strip()
            try:
                self.discord_cooldown = max(5, int(ent_cool.get().strip()))
            except ValueError:
                self.discord_cooldown = 60
            self.save_config()
            dlg.destroy()

        btn_box = tk.Frame(dlg, pady=10)
        btn_box.pack(fill=tk.X, padx=20)

        tk.Button(btn_box, text="🧪 Send Test Alert", font=("Segoe UI", 9, "bold"), command=test_webhook).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_box, text="Save & Close", font=("Segoe UI", 9, "bold"), bg="#5cb85c", fg="#ffffff", command=save_and_close).pack(side=tk.RIGHT, padx=10)

    def open_calibration_dialog(self):
        """Opens a calibration curve editor dialog."""
        dlg = tk.Toplevel(self.root)
        dlg.title("📐 Sensor Calibration Curve")
        dlg.geometry("620x560")
        dlg.resizable(True, True)
        dlg.grab_set()

        tk.Label(
            dlg,
            text="📐 Piecewise Linear Calibration Curve",
            font=("Segoe UI", 13, "bold")
        ).pack(pady=(14, 2))

        tk.Label(
            dlg,
            text="Enter pairs of (Raw Sensor Reading) → (Calibrated Reference Reading) from your reference meter.",
            font=("Segoe UI", 9),
            fg="#555555",
            wraplength=580
        ).pack(pady=(0, 8))

        tk.Label(
            dlg,
            text="The curve interpolates linearly between points and extrapolates at the edges.",
            font=("Segoe UI", 9, "italic"),
            fg="#777777"
        ).pack(pady=(0, 8))

        # Column headers
        hdr = tk.Frame(dlg, padx=20)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Raw Reading (dB)", font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        tk.Label(hdr, text="Calibrated Reference (dB)", font=("Segoe UI", 9, "bold"), width=22, anchor="w").pack(side=tk.LEFT)

        # Scrollable list frame
        list_outer = tk.Frame(dlg, bd=1, relief=tk.SUNKEN)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=4)

        canvas_scroll = tk.Canvas(list_outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=canvas_scroll.yview)
        canvas_scroll.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas_scroll.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner_frame = tk.Frame(canvas_scroll)
        inner_window = canvas_scroll.create_window((0, 0), window=inner_frame, anchor="nw")

        def on_frame_configure(e):
            canvas_scroll.configure(scrollregion=canvas_scroll.bbox("all"))
        inner_frame.bind("<Configure>", on_frame_configure)

        def on_canvas_configure(e):
            canvas_scroll.itemconfig(inner_window, width=e.width)
        canvas_scroll.bind("<Configure>", on_canvas_configure)

        # Row entries list [(raw_var, cal_var, row_frame), ...]
        rows = []

        def add_row(raw_val="", cal_val=""):
            row_f = tk.Frame(inner_frame, pady=2)
            row_f.pack(fill=tk.X, padx=6)

            raw_var = tk.StringVar(value=str(raw_val))
            cal_var = tk.StringVar(value=str(cal_val))

            ent_raw = ttk.Entry(row_f, textvariable=raw_var, width=16)
            ent_raw.pack(side=tk.LEFT, padx=(0, 12))

            tk.Label(row_f, text="→", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 8))

            ent_cal = ttk.Entry(row_f, textvariable=cal_var, width=16)
            ent_cal.pack(side=tk.LEFT, padx=(0, 12))

            def remove_this_row(rf=row_f, r=(None,)):
                for i, (rv, cv, rf2) in enumerate(rows):
                    if rf2 is rf:
                        rows.pop(i)
                        rf.destroy()
                        break

            tk.Button(
                row_f, text="✕", font=("Segoe UI", 8), fg="#cc0000",
                relief=tk.FLAT, padx=4, command=remove_this_row
            ).pack(side=tk.LEFT)

            rows.append((raw_var, cal_var, row_f))
            canvas_scroll.update_idletasks()
            canvas_scroll.yview_moveto(1.0)

        # Pre-populate with existing calibration points
        existing = sorted(self.calibration_points, key=lambda p: p[0])
        for pt in existing:
            add_row(f"{pt[0]:.1f}", f"{pt[1]:.1f}")

        # If no points yet, seed with a blank row
        if not existing:
            add_row()

        # Enable toggle
        var_enabled = tk.BooleanVar(value=self.calibration_enabled)

        # Bottom controls
        bottom = tk.Frame(dlg, padx=20, pady=8)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)

        status_lbl = tk.Label(dlg, text="", font=("Segoe UI", 9, "bold"), fg="#c0392b")
        status_lbl.pack(side=tk.BOTTOM, pady=(0, 2))

        chk_enable = tk.Checkbutton(
            bottom,
            text="Apply calibration correction to all readings",
            variable=var_enabled,
            font=("Segoe UI", 9, "bold")
        )
        chk_enable.pack(side=tk.LEFT)

        def add_blank_row():
            add_row()

        def save_and_close():
            pts = []
            for raw_var, cal_var, _ in rows:
                raw_s = raw_var.get().strip()
                cal_s = cal_var.get().strip()
                if not raw_s and not cal_s:
                    continue  # skip blank rows
                try:
                    raw_f = float(raw_s)
                    cal_f = float(cal_s)
                    pts.append([raw_f, cal_f])
                except ValueError:
                    status_lbl.config(text=f"⚠ Invalid value — all entries must be numbers (e.g. 67.5)")
                    return

            # Check for duplicate raw values
            raw_vals = [p[0] for p in pts]
            if len(raw_vals) != len(set(raw_vals)):
                status_lbl.config(text="⚠ Duplicate raw dB values detected — each raw reading must be unique.")
                return

            pts.sort(key=lambda p: p[0])
            self.calibration_points = pts
            self.calibration_enabled = var_enabled.get()
            self.save_config()
            self._update_cal_badge()
            dlg.destroy()

        btn_row = tk.Frame(bottom)
        btn_row.pack(side=tk.RIGHT)

        tk.Button(
            btn_row, text="+ Add Point",
            font=("Segoe UI", 9, "bold"),
            command=add_blank_row
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            btn_row, text="Save & Apply",
            font=("Segoe UI", 9, "bold"),
            bg="#27ae60", fg="#ffffff",
            command=save_and_close
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            btn_row, text="Cancel",
            font=("Segoe UI", 9, "bold"),
            command=dlg.destroy
        ).pack(side=tk.LEFT, padx=6)

    def apply_serial_settings(self):
        port = self.cbo_port.get().strip()
        try:
            baud = int(self.cbo_baud.get())
            slave = int(self.cbo_slave.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid Baud Rate or Slave ID!")
            return

        self.lbl_mode_badge.config(
            text=f"CONNECTING ({port})...",
            bg="#f0ad4e",
            fg="#ffffff"
        )
        self.reader_thread.set_config(port=port, baudrate=baud, slave_addr=slave)
        # Also switch source to serial if it wasn't already
        if self.source_mode != 'serial':
            self.set_source_mode('serial')
            self._var_source.set('serial')
            self._apply_source_widget_visibility()
        self.save_config()

    def on_theme_change(self, event=None):
        self.theme_name = self.cbo_theme.get()
        self.apply_theme()
        self.save_config()
        self.redraw_lcd()

    def apply_theme(self):
        t = THEMES[self.theme_name]
        is_alarm_flash = self.alarm_active and self.flash_state

        bg = t['alarm_flash'] if is_alarm_flash else t['bg']
        panel_bg = t['alarm_bg'] if is_alarm_flash else t['panel_bg']
        text_color = t['text']

        self.main_frame.config(bg=bg)
        self.header_frame.config(bg=bg)
        self.lbl_title.config(bg=bg, fg=text_color)
        self.status_container.config(bg=bg)
        self.lbl_comm_led.config(bg=bg)

        self.lcd_outer_frame.config(bg=t['bezel_border'])
        self.canvas.config(bg=panel_bg)

        self.stats_frame.config(bg=bg)
        self.control_frame.config(bg=bg)

        for widget in self.control_frame.winfo_children():
            if isinstance(widget, (tk.Label, tk.Checkbutton)):
                widget.config(bg=bg, fg=text_color)

        self._update_cal_badge()

    def _update_cal_badge(self):
        """Updates the Calibration button to indicate active/inactive state."""
        if not hasattr(self, 'btn_cal'):
            return
        if self.calibration_enabled and len(self.calibration_points) >= 2:
            self.btn_cal.config(text="📐 CAL ACTIVE", bg="#27ae60", activebackground="#1e8449")
        else:
            self.btn_cal.config(text="📐 Calibration", bg="#e67e22", activebackground="#ca6f1e")

    def reset_statistics(self):
        self.min_db = 999.9
        self.max_db = 0.0
        self.sample_count = 0
        self.sum_db = 0.0
        self.stat_labels['val_min'].config(text="0.0 dB")
        self.stat_labels['val_avg'].config(text="0.0 dB")
        self.stat_labels['val_max'].config(text="0.0 dB")

    # ==========================================
    # Vector 7-Segment Canvas Renderer
    # ==========================================
    def draw_segment(self, canvas, points, is_on, on_color, off_color):
        """Draws a single 7-segment polygon segment."""
        color = on_color if is_on else off_color
        canvas.create_polygon(points, fill=color, outline="")

    def draw_7seg_digit(self, canvas, char, x, y, width, height, on_color, off_color, slant_deg=6):
        """
        Renders a vector 7-segment character with slanted LCD style.
        Segment mapping:
             AAA
            F   B
             GGG
            E   C
             DDD  . DP
        """
        SEG_MAP = {
            '0': (1, 1, 1, 1, 1, 1, 0),
            '1': (0, 1, 1, 0, 0, 0, 0),
            '2': (1, 1, 0, 1, 1, 0, 1),
            '3': (1, 1, 1, 1, 0, 0, 1),
            '4': (0, 1, 1, 0, 0, 1, 1),
            '5': (1, 0, 1, 1, 0, 1, 1),
            '6': (1, 0, 1, 1, 1, 1, 1),
            '7': (1, 1, 1, 0, 0, 0, 0),
            '8': (1, 1, 1, 1, 1, 1, 1),
            '9': (1, 1, 1, 1, 0, 1, 1),
            '-': (0, 0, 0, 0, 0, 0, 1),
            ' ': (0, 0, 0, 0, 0, 0, 0),
        }

        states = SEG_MAP.get(char, (0, 0, 0, 0, 0, 0, 0))

        tan_a = math.tan(math.radians(slant_deg))
        def p(px, py):
            shear_x = px + (y + height - py) * tan_a
            return (shear_x, py)

        thickness = width * 0.16
        gap = thickness * 0.15

        w = width
        h = height
        t = thickness

        seg_A = [
            p(x + t/2 + gap, y),
            p(x + w - t/2 - gap, y),
            p(x + w - t - gap, y + t),
            p(x + t + gap, y + t)
        ]

        seg_B = [
            p(x + w - t, y + t/2 + gap),
            p(x + w, y + t/2 + gap),
            p(x + w, y + h/2 - gap/2),
            p(x + w - t, y + h/2 - t/2 - gap/2)
        ]

        seg_C = [
            p(x + w - t, y + h/2 + t/2 + gap/2),
            p(x + w, y + h/2 + gap/2),
            p(x + w, y + h - t/2 - gap),
            p(x + w - t, y + h - t - gap)
        ]

        seg_D = [
            p(x + t + gap, y + h - t),
            p(x + w - t - gap, y + h - t),
            p(x + w - t/2 - gap, y + h),
            p(x + t/2 + gap, y + h)
        ]

        seg_E = [
            p(x, y + h/2 + gap/2),
            p(x + t, y + h/2 + t/2 + gap/2),
            p(x + t, y + h - t - gap),
            p(x, y + h - t/2 - gap)
        ]

        seg_F = [
            p(x, y + t/2 + gap),
            p(x + t, y + t/2 + gap),
            p(x + t, y + h/2 - t/2 - gap/2),
            p(x, y + h/2 - gap/2)
        ]

        seg_G = [
            p(x + t + gap, y + h/2 - t/2),
            p(x + w - t - gap, y + h/2 - t/2),
            p(x + w - t/2 - gap, y + h/2 + t/2),
            p(x + t/2 + gap, y + h/2 + t/2)
        ]

        segs = [seg_A, seg_B, seg_C, seg_D, seg_E, seg_F, seg_G]
        for i, seg_pts in enumerate(segs):
            flat_pts = [coord for pt in seg_pts for coord in pt]
            self.draw_segment(canvas, flat_pts, states[i], on_color, off_color)

    def draw_decimal_point(self, canvas, x, y, size, is_on, on_color, off_color):
        color = on_color if is_on else off_color
        canvas.create_oval(x, y, x + size, y + size, fill=color, outline="")

    # ==========================================
    # Main LCD Display Redraw Loop
    # ==========================================
    def redraw_lcd(self):
        self.canvas.delete("all")

        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()

        if cw < 50 or ch < 50:
            return

        t = THEMES[self.theme_name]
        is_alarm_flash = self.alarm_active and self.flash_state

        on_color = t['alarm_on'] if is_alarm_flash else t['on_color']
        off_color = t['off_color']

        # Format DB string (e.g., " 106.4" or "  54.2")
        db_str = f"{self.current_db:5.1f}"
        if len(db_str) < 5:
            db_str = db_str.rjust(5)

        # ----------------------------------
        # 1. Render Large 7-Segment Digits
        # ----------------------------------
        digit_count = 4  # 3 integer digits + 1 decimal digit (e.g. [1][0][6] . [5])
        digit_width = max(35, min(cw * 0.13, ch * 0.40))
        digit_height = digit_width * 1.8
        digit_spacing = digit_width * 0.35

        total_digits_width = (digit_count * digit_width) + ((digit_count - 1) * digit_spacing) + (digit_width * 0.6)
        start_x = (cw - total_digits_width) / 2
        start_y = ch * 0.18

        # Draw characters
        parts = db_str.split('.')
        int_part = parts[0].rjust(3)
        dec_part = parts[1][0] if len(parts) > 1 else '0'

        curr_x = start_x

        # Render 3 Integer Digits
        for ch_char in int_part:
            self.draw_7seg_digit(self.canvas, ch_char, curr_x, start_y, digit_width, digit_height, on_color, off_color)
            curr_x += digit_width + digit_spacing

        # Draw Decimal Point (DP)
        dp_size = digit_width * 0.22
        dp_x = curr_x - (digit_spacing * 0.7)
        dp_y = start_y + digit_height - dp_size
        self.draw_decimal_point(self.canvas, dp_x, dp_y, dp_size, True, on_color, off_color)

        curr_x += dp_size * 0.5

        # Render 1 Decimal Digit
        self.draw_7seg_digit(self.canvas, dec_part, curr_x, start_y, digit_width, digit_height, on_color, off_color)
        curr_x += digit_width + (digit_spacing * 0.8)

        # Draw "dB" Unit Text Label
        unit_x = curr_x + 10
        unit_y = start_y + (digit_height * 0.55)
        self.canvas.create_text(
            unit_x, unit_y,
            text="dB",
            font=("Segoe UI", int(digit_height * 0.28), "bold"),
            fill=on_color,
            anchor="w"
        )

        # Draw CAL indicator badge when calibration is active
        if self.calibration_enabled and len(self.calibration_points) >= 2:
            cal_font_size = max(8, int(digit_height * 0.13))
            cal_badge_x = unit_x
            cal_badge_y = unit_y + digit_height * 0.28
            self.canvas.create_text(
                cal_badge_x, cal_badge_y,
                text="CAL",
                font=("Segoe UI", cal_font_size, "bold"),
                fill="#27ae60",
                anchor="w"
            )

        # ----------------------------------
        # 2. Render Bottom Bar Graph & Peak Hold
        # ----------------------------------
        bar_y = start_y + digit_height + (ch * 0.12)
        bar_height = max(18, ch * 0.08)
        bar_start_x = cw * 0.08
        bar_end_x = cw * 0.92
        bar_width = bar_end_x - bar_start_x

        min_db, max_db = 30.0, 110.0
        clamped_val = max(min_db, min(self.current_db, max_db))
        ratio = (clamped_val - min_db) / (max_db - min_db)

        # Scale Peak Hold
        if self.current_db > self.peak_hold_db:
            self.peak_hold_db = self.current_db
            self.peak_hold_timer = 15
        else:
            if self.peak_hold_timer > 0:
                self.peak_hold_timer -= 1
            else:
                self.peak_hold_db = max(self.current_db, self.peak_hold_db - 0.8)

        peak_ratio = (max(min_db, min(self.peak_hold_db, max_db)) - min_db) / (max_db - min_db)

        # Draw Bar Background Frame
        self.canvas.create_rectangle(
            bar_start_x - 4, bar_y - 4, bar_end_x + 4, bar_y + bar_height + 4,
            outline=t['bezel_border'], fill="#050a06", width=2
        )

        # Segmented Bar Graph (35 blocks)
        num_segments = 35
        seg_w = (bar_width - (num_segments * 3)) / num_segments
        filled_segments = int(ratio * num_segments)
        peak_seg = int(peak_ratio * (num_segments - 1))

        for i in range(num_segments):
            sx = bar_start_x + i * (seg_w + 3)
            ex = sx + seg_w
            sy = bar_y
            ey = bar_y + bar_height

            seg_ratio = i / num_segments
            if seg_ratio < 0.65:
                seg_color = "#00FF66"
            elif seg_ratio < 0.88:
                seg_color = "#FFCC00"
            else:
                seg_color = "#FF2222"

            if i < filled_segments:
                self.canvas.create_rectangle(sx, sy, ex, ey, fill=seg_color, outline="")
            else:
                self.canvas.create_rectangle(sx, sy, ex, ey, fill="#112014", outline="")

            if i == peak_seg and peak_ratio > 0.05:
                self.canvas.create_rectangle(sx, sy - 3, ex, ey + 3, fill="#FFFFFF", outline="#FFD700")

        # Bar Graph Scale Labels
        self.canvas.create_text(bar_start_x, bar_y + bar_height + 14, text="30 dB", font=("Segoe UI", 9, "bold"), fill=t['subtext'], anchor="w")
        self.canvas.create_text(bar_start_x + (bar_width * 0.625), bar_y + bar_height + 14, text="80 dB", font=("Segoe UI", 9, "bold"), fill=t['subtext'], anchor="c")
        self.canvas.create_text(bar_start_x + (bar_width * 0.9375), bar_y + bar_height + 14, text="105 dB 🚨", font=("Segoe UI", 9, "bold"), fill="#FF5555", anchor="c")
        self.canvas.create_text(bar_end_x, bar_y + bar_height + 14, text="110 dB", font=("Segoe UI", 9, "bold"), fill=t['subtext'], anchor="e")

    # ==========================================
    # Flashing Alarm & Update Timer Loops
    # ==========================================
    def update_flash_cycle(self):
        """Toggles flash state for red alarm visual effect when > 105 dB."""
        if self.alarm_active:
            self.flash_state = not self.flash_state
            self.apply_theme()
            self.redraw_lcd()
            if self.flash_state:
                self.alarm_banner.pack(fill=tk.X, before=self.canvas)
            else:
                self.alarm_banner.pack_forget()
        else:
            if self.flash_state or self.alarm_banner.winfo_ismapped():
                self.flash_state = False
                self.alarm_banner.pack_forget()
                self.apply_theme()
                self.redraw_lcd()

        self.root.after(200, self.update_flash_cycle)

    def process_queue(self):
        """Processes telemetry data coming from ModbusReaderThread."""
        try:
            while True:
                msg = self.data_queue.get_nowait()
                mode = msg.get('mode', 'HARDWARE')
                port = msg.get('port', self.cbo_port.get().strip())

                if msg['status'] == 'OK':
                    raw_val = msg['db']
                    # Apply piecewise linear calibration if enabled
                    if self.calibration_enabled and len(self.calibration_points) >= 2:
                        val = self.apply_calibration(raw_val)
                    else:
                        val = raw_val
                    self.current_db = val

                    # Update statistics
                    if val < self.min_db:
                        self.min_db = val
                    if val > self.max_db:
                        self.max_db = val

                    self.sample_count += 1
                    self.sum_db += val
                    avg_db = self.sum_db / self.sample_count

                    self.stat_labels['val_cur'].config(text=f"{val:5.1f} dB")
                    self.stat_labels['val_min'].config(text=f"{self.min_db:5.1f} dB")
                    self.stat_labels['val_max'].config(text=f"{self.max_db:5.1f} dB")
                    self.stat_labels['val_avg'].config(text=f"{avg_db:5.1f} dB")

                    # Check Alarm (> 105 dB)
                    if val >= self.alarm_threshold:
                        if not self.alarm_active:
                            self.alarm_active = True
                            self.lbl_alarm_badge.config(bg="#FF0000", fg="#FFFFFF", text="🚨 ALARM: >105 dB!")

                        # Trigger Discord Webhook Notification if enabled and cooldown passed
                        if self.discord_enabled and self.discord_webhook_url:
                            now = time.time()
                            if now - self.last_discord_alert_time >= self.discord_cooldown:
                                self.last_discord_alert_time = now
                                url = self.discord_webhook_url
                                uid = self.discord_user_id
                                thresh = self.alarm_threshold
                                threading.Thread(
                                    target=send_discord_webhook,
                                    args=(url, val, thresh, uid, False),
                                    daemon=True
                                ).start()
                    else:
                        if self.alarm_active:
                            self.alarm_active = False
                            self.lbl_alarm_badge.config(bg="#222222", fg="#888888", text="ALARM: >105 dB")

                    # Mode & Status Badges for OK packets
                    source = msg.get('source', 'serial')
                    port_label = msg.get('port', port)
                    if source in ('udp', 'tcp'):
                        net_label = 'UDP' if source == 'udp' else 'TCP'
                        self.lbl_mode_badge.config(
                            text=f"{net_label}: {port_label}",
                            bg="#17a2b8", fg="#ffffff"
                        )
                        self.lbl_comm_led.config(text=f"● NET ONLINE ({port_label})", fg="#00E5FF")
                    else:
                        self.lbl_mode_badge.config(text=f"HARDWARE ({port_label})", bg="#5cb85c", fg="#ffffff")
                        self.lbl_comm_led.config(text=f"● ONLINE ({port_label})", fg="#00FF66")
                    self.redraw_lcd()

                elif msg['status'] in ('ERROR', 'TIMEOUT'):
                    err_msg = msg.get('error', 'Serial Timeout')
                    source = msg.get('source', 'serial')
                    port_label = msg.get('port', port)
                    if source in ('udp', 'tcp'):
                        self.lbl_mode_badge.config(
                            text=f"NET: NO SIGNAL ({port_label})",
                            bg="#d9534f", fg="#ffffff"
                        )
                        self.lbl_comm_led.config(text=f"● OFFLINE ({port_label})", fg="#FF3333")
                    else:
                        self.lbl_mode_badge.config(text=f"CONNECTING ({port_label})...", bg="#f0ad4e", fg="#ffffff")
                        self.lbl_comm_led.config(text=f"● NO RESPONSE ({port_label})", fg="#FF3333")
                    self.stat_labels['val_cur'].config(text="0.0 dB")
                    self.current_db = 0.0
                    self.redraw_lcd()

        except queue.Empty:
            pass

        self.root.after(50, self.process_queue)


# ==========================================
# Main Execution Entry Point
# ==========================================
def main():
    root = tk.Tk()
    app = SoundMeterApp(root)

    def on_close():
        app.reader_thread.stop()
        if app._net_thread and app._net_thread.is_alive():
            app._net_thread.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
