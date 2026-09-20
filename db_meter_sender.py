#!/usr/bin/env python3
"""
db_meter_sender.py  —  Headless Modbus RTU dB Meter Network Sender
Designed for Raspberry Pi 3B+ with USB RS485 adapter.

Reads sound level data from a Modbus RTU noise sensor and streams it over:
  - UDP Broadcast (LAN auto-discovery, no client config needed)
  - TCP Server (direct connections across subnets, multiple clients)

Both modes run simultaneously by default.

Usage:
    python3 db_meter_sender.py [options]

Options:
    --serial-port   /dev/ttyUSB0    Serial port for RS485 adapter
    --baud          9600            Baud rate
    --slave         1               Modbus slave address
    --udp-port      55123           UDP broadcast port (0 to disable)
    --tcp-port      55124           TCP server port (0 to disable)
    --interval      0.2             Poll interval in seconds
    --config        sender_config.json   Config file path
    --save-config               Save current args to config file and exit

Config file (sender_config.json) is loaded on startup. CLI args override it.
"""

import sys
import os
import time
import json
import socket
import struct
import threading
import argparse
import signal
import queue
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    print("[WARN] pyserial not installed. Run: pip3 install pyserial", flush=True)


# ==========================================
# Constants
# ==========================================
DEFAULT_UDP_PORT = 55123
DEFAULT_TCP_PORT = 55124
DEFAULT_BAUD     = 9600
DEFAULT_SLAVE    = 1
DEFAULT_INTERVAL = 0.2
SENDER_NAME      = "pi-meter"
VERSION          = "1.0.0"


# ==========================================
# Modbus RTU CRC16 Utility
# ==========================================
def calculate_crc(data: bytes) -> bytes:
    """Calculates standard Modbus RTU CRC16 and appends to data."""
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
# Config File Helpers
# ==========================================
def load_config(config_path: str) -> dict:
    """Loads config from JSON file; returns empty dict if missing or invalid."""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Could not read config file {config_path}: {e}", flush=True)
    return {}


def save_config(config_path: str, cfg: dict):
    """Saves config dict to JSON file."""
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        print(f"[INFO] Config saved to: {config_path}", flush=True)
    except Exception as e:
        print(f"[ERROR] Could not save config: {e}", flush=True)


def detect_serial_port() -> str:
    """Auto-detects the first available USB serial port."""
    if HAS_SERIAL:
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if ports:
            return ports[0]
    # Fallback defaults for Pi
    for p in ["/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyUSB1", "/dev/ttyACM1"]:
        if os.path.exists(p):
            return p
    return "/dev/ttyUSB0"


# ==========================================
# TCP Server — handles multiple clients
# ==========================================
class TCPServer(threading.Thread):
    """
    Lightweight TCP server that accepts multiple client connections and
    broadcasts JSON readings to all connected clients.
    """
    def __init__(self, port: int):
        super().__init__(daemon=True, name="TCPServer")
        self.port = port
        self.clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._running = True
        self._server_sock = None

    def run(self):
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.settimeout(1.0)
            self._server_sock.bind(("0.0.0.0", self.port))
            self._server_sock.listen(10)
            print(f"[TCP ] Server listening on port {self.port}", flush=True)
        except OSError as e:
            print(f"[TCP ] Failed to bind port {self.port}: {e}", flush=True)
            return

        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
                client_sock.settimeout(5.0)
                with self._clients_lock:
                    self.clients.append(client_sock)
                print(f"[TCP ] Client connected: {addr[0]}:{addr[1]}  (total: {len(self.clients)})", flush=True)
            except socket.timeout:
                continue
            except OSError:
                break

        self._cleanup()

    def send_to_all(self, payload_bytes: bytes):
        """Sends payload to all connected TCP clients; removes dead ones."""
        dead = []
        with self._clients_lock:
            for sock in self.clients:
                try:
                    sock.sendall(payload_bytes)
                except (OSError, BrokenPipeError, ConnectionResetError):
                    dead.append(sock)
            for sock in dead:
                self.clients.remove(sock)
                try:
                    sock.close()
                except Exception:
                    pass
        if dead:
            print(f"[TCP ] Removed {len(dead)} dead client(s). Remaining: {len(self.clients)}", flush=True)

    def client_count(self) -> int:
        with self._clients_lock:
            return len(self.clients)

    def stop(self):
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    def _cleanup(self):
        with self._clients_lock:
            for sock in self.clients:
                try:
                    sock.close()
                except Exception:
                    pass
            self.clients.clear()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass


# ==========================================
# UDP Broadcaster
# ==========================================
class UDPBroadcaster:
    """Sends UDP broadcast packets to the local subnet."""
    def __init__(self, port: int):
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        print(f"[UDP ] Broadcaster ready on port {port}", flush=True)

    def send(self, payload_bytes: bytes):
        try:
            self._sock.sendto(payload_bytes, ("255.255.255.255", self.port))
        except OSError as e:
            print(f"[UDP ] Send error: {e}", flush=True)

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


# ==========================================
# Modbus RTU Serial Reader
# ==========================================
class ModbusReader:
    """Manages serial connection and Modbus RTU reads."""
    def __init__(self, port: str, baudrate: int, slave_addr: int, timeout: float = 0.2):
        self.port = port
        self.baudrate = baudrate
        self.slave_addr = slave_addr
        self.timeout = timeout
        self._ser = None

    def _connect(self):
        """Opens the serial port."""
        if self._ser and self._ser.is_open:
            return True
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self.timeout
            )
            print(f"[SERIAL] Opened {self.port} @ {self.baudrate} baud, slave {self.slave_addr}", flush=True)
            return True
        except Exception as e:
            print(f"[SERIAL] Cannot open {self.port}: {e}", flush=True)
            self._ser = None
            return False

    def read(self) -> dict:
        """
        Performs one Modbus RTU read cycle.
        Returns dict: {'status': 'OK'|'ERROR'|'TIMEOUT', 'db': float, 'error': str}
        """
        if not HAS_SERIAL:
            return {"status": "ERROR", "db": 0.0, "error": "pyserial not installed"}

        if not self._connect():
            return {"status": "ERROR", "db": 0.0, "error": f"Cannot open {self.port}"}

        try:
            query_payload = bytes([self.slave_addr, 0x03, 0x00, 0x00, 0x00, 0x01])
            query_frame = calculate_crc(query_payload)

            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
            self._ser.write(query_frame)
            self._ser.flush()

            time.sleep(0.06)
            response = self._ser.read(7)

            if len(response) >= 5 and response[1] == 0x03:
                raw_val = (response[3] << 8) | response[4]
                db_val = raw_val / 10.0
                return {"status": "OK", "db": db_val, "error": ""}
            else:
                return {
                    "status": "TIMEOUT",
                    "db": 0.0,
                    "error": f"No Modbus response from slave {self.slave_addr} on {self.port}"
                }

        except Exception as e:
            self._close()
            return {"status": "ERROR", "db": 0.0, "error": str(e)}

    def _close(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def close(self):
        self._close()


# ==========================================
# Main Sender Loop
# ==========================================
class Sender:
    def __init__(self, args):
        self.args = args
        self._running = True
        self._udp: UDPBroadcaster | None = None
        self._tcp: TCPServer | None = None
        self._reader: ModbusReader | None = None

    def _build_packet(self, result: dict) -> bytes:
        """Builds a newline-terminated JSON packet from a read result."""
        payload = {
            "db":          result["db"],
            "status":      result["status"],
            "error":       result.get("error", ""),
            "timestamp":   datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "sender":      SENDER_NAME,
            "serial_port": self.args.serial_port,
            "version":     VERSION,
        }
        return (json.dumps(payload) + "\n").encode("utf-8")

    def run(self):
        a = self.args

        # Init serial reader
        self._reader = ModbusReader(
            port=a.serial_port,
            baudrate=a.baud,
            slave_addr=a.slave,
        )

        # Init UDP broadcaster
        if a.udp_port > 0:
            try:
                self._udp = UDPBroadcaster(a.udp_port)
            except Exception as e:
                print(f"[UDP ] Failed to init broadcaster: {e}", flush=True)

        # Init TCP server
        if a.tcp_port > 0:
            self._tcp = TCPServer(a.tcp_port)
            self._tcp.start()

        # Print startup summary
        print("=" * 55, flush=True)
        print(f"  dB Meter Network Sender v{VERSION}", flush=True)
        print(f"  Serial : {a.serial_port} @ {a.baud} baud  Slave: {a.slave}", flush=True)
        if a.udp_port > 0:
            print(f"  UDP    : Broadcasting on port {a.udp_port}", flush=True)
        if a.tcp_port > 0:
            print(f"  TCP    : Server on port {a.tcp_port}", flush=True)
        print(f"  Interval: {a.interval}s", flush=True)
        print("=" * 55, flush=True)
        print("Press Ctrl+C to stop.\n", flush=True)

        last_status = None
        last_db = 0.0
        sample_count = 0
        error_streak = 0

        while self._running:
            loop_start = time.monotonic()

            result = self._reader.read()
            pkt = self._build_packet(result)

            # Broadcast UDP
            if self._udp:
                self._udp.send(pkt)

            # Send to all TCP clients
            if self._tcp:
                self._tcp.send_to_all(pkt)

            # Console status line
            tcp_clients = self._tcp.client_count() if self._tcp else 0
            status_tag = result["status"]
            db_val = result["db"]

            if status_tag == "OK":
                error_streak = 0
                sample_count += 1
                indicator = "✓" if sys.stdout.encoding and "UTF" in sys.stdout.encoding.upper() else "OK"
                print(
                    f"\r  dB: {db_val:6.1f}  |  {indicator} ONLINE  |  "
                    f"TCP clients: {tcp_clients}  |  Samples: {sample_count}   ",
                    end="",
                    flush=True
                )
            else:
                error_streak += 1
                err_txt = result.get("error", "Unknown error")
                if error_streak <= 3 or error_streak % 10 == 0:
                    print(
                        f"\r  [{status_tag}] {err_txt[:55]}   ",
                        end="",
                        flush=True
                    )

            # Sleep for remainder of interval
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.0, a.interval - elapsed)
            time.sleep(sleep_time)

        self._shutdown()

    def stop(self):
        self._running = False

    def _shutdown(self):
        print("\n[INFO] Shutting down...", flush=True)
        if self._reader:
            self._reader.close()
        if self._udp:
            self._udp.close()
        if self._tcp:
            self._tcp.stop()
        print("[INFO] Stopped.", flush=True)


# ==========================================
# Argument Parsing & Entry Point
# ==========================================
def parse_args(config: dict) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless Modbus RTU dB Meter Network Sender for Raspberry Pi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 db_meter_sender.py
  python3 db_meter_sender.py --serial-port /dev/ttyUSB0 --baud 9600 --slave 1
  python3 db_meter_sender.py --udp-port 55123 --tcp-port 55124
  python3 db_meter_sender.py --save-config
        """
    )

    default_port = config.get("serial_port", detect_serial_port())
    parser.add_argument("--serial-port", default=default_port,
                        help=f"RS485 serial port (default: {default_port})")
    parser.add_argument("--baud", type=int, default=int(config.get("baud", DEFAULT_BAUD)),
                        help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--slave", type=int, default=int(config.get("slave", DEFAULT_SLAVE)),
                        help=f"Modbus slave address (default: {DEFAULT_SLAVE})")
    parser.add_argument("--udp-port", type=int, default=int(config.get("udp_port", DEFAULT_UDP_PORT)),
                        help=f"UDP broadcast port, 0 to disable (default: {DEFAULT_UDP_PORT})")
    parser.add_argument("--tcp-port", type=int, default=int(config.get("tcp_port", DEFAULT_TCP_PORT)),
                        help=f"TCP server port, 0 to disable (default: {DEFAULT_TCP_PORT})")
    parser.add_argument("--interval", type=float, default=float(config.get("interval", DEFAULT_INTERVAL)),
                        help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("--config", default="sender_config.json",
                        help="Path to config JSON file (default: sender_config.json)")
    parser.add_argument("--save-config", action="store_true",
                        help="Save current settings to config file and exit")

    return parser.parse_args()


def main():
    # Pre-parse just --config to load the file before full parse
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default="sender_config.json")
    pre_args, _ = pre.parse_known_args()
    config_path = pre_args.config

    # Determine config file location relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, config_path)

    cfg = load_config(config_path)
    args = parse_args(cfg)
    args.config = config_path  # store resolved path

    if args.save_config:
        save_config(config_path, {
            "serial_port": args.serial_port,
            "baud":        args.baud,
            "slave":       args.slave,
            "udp_port":    args.udp_port,
            "tcp_port":    args.tcp_port,
            "interval":    args.interval,
        })
        sys.exit(0)

    sender = Sender(args)

    def _signal_handler(sig, frame):
        print("\n[INFO] Interrupt received.", flush=True)
        sender.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    sender.run()


if __name__ == "__main__":
    main()
