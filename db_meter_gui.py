#!/usr/bin/env python3
"""
Graphical LCD 7-Segment dB Sound Level Meter
Cross-Platform (Windows / Linux) GUI for Modbus RTU Noise Sensor
Features: Vector 7-segment display, Flashing Red Alarm (>105 dB), Bar Meter, Peak Hold, Statistics, Simulation Mode
"""

import sys
import os
import time
import math
import random
import threading
import queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

# Try importing pyserial; if missing or on error, serial operations switch to simulation
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
# Serial / Modbus Reader Worker Thread
# ==========================================
class ModbusReaderThread(threading.Thread):
    def __init__(self, data_queue, port="/dev/ttyACM0", baudrate=9600, slave_addr=1, poll_interval=0.2, simulation_mode=True):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.port = port
        self.baudrate = baudrate
        self.slave_addr = slave_addr
        self.poll_interval = poll_interval
        self.running = True
        self.simulation_mode = simulation_mode or not HAS_SERIAL
        self.ser = None
        self._lock = threading.Lock()
        self._wake_event = threading.Event()

        # Simulation noise generator variables
        self._sim_base = 65.0
        self._sim_target = 65.0
        self._sim_alarm_timer = 0

    def set_config(self, port, baudrate, simulation_mode, slave_addr=1):
        with self._lock:
            self.port = port
            self.baudrate = baudrate
            self.simulation_mode = simulation_mode
            self.slave_addr = slave_addr
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
        # Drain any leftover stale messages from data queue
        try:
            while not self.data_queue.empty():
                self.data_queue.get_nowait()
        except Exception:
            pass
        self._wake_event.set()

    def trigger_sim_spike(self):
        """Forces a high dB spike in simulation mode to test >105 dB alarm."""
        with self._lock:
            val = random.uniform(106.5, 114.0)
            self._sim_base = val
            self._sim_target = val
        self._wake_event.set()

    def stop(self):
        self.running = False
        self._wake_event.set()

    def run(self):
        while self.running:
            self._wake_event.clear()
            with self._lock:
                is_sim = self.simulation_mode or not HAS_SERIAL
                port = self.port
                baudrate = self.baudrate
                slave_addr = self.slave_addr

            if is_sim:
                # Generate realistic fluctuating sound level
                if random.random() < 0.05:
                    self._sim_target = random.choice([
                        random.uniform(45.0, 75.0),
                        random.uniform(85.0, 98.0),
                        random.uniform(105.5, 112.5)
                    ])
                
                diff = self._sim_target - self._sim_base
                self._sim_base += diff * 0.15 + random.uniform(-0.4, 0.4)
                self._sim_base = max(30.0, min(120.0, self._sim_base))
                
                db_val = round(self._sim_base, 1)
                self.data_queue.put({
                    'status': 'OK',
                    'db': db_val,
                    'mode': 'SIMULATION',
                    'port': port,
                    'timestamp': datetime.now().strftime("%H:%M:%S")
                })
                self._wake_event.wait(self.poll_interval)
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

                # Frame: [SlaveID, Func(0x03), RegHi, RegLo, CountHi, CountLo]
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
                        'mode': 'HARDWARE',
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

        # Application state
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

        # Queue & Thread
        self.data_queue = queue.Queue()
        default_port = self.detect_default_port()
        self.reader_thread = ModbusReaderThread(
            data_queue=self.data_queue,
            port=default_port,
            baudrate=9600,
            simulation_mode=True
        )

        self.setup_ui()
        self.apply_theme()

        # Start background thread and update timer loop
        self.reader_thread.start()
        self.root.after(50, self.process_queue)
        self.root.after(200, self.update_flash_cycle)

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
            text="SIMULATION MODE" if not HAS_SERIAL else "HARDWARE",
            font=("Segoe UI", 9, "bold"),
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

        # Serial Port Selector
        tk.Label(self.control_frame, text="Port:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(5, 2))
        self.cbo_port = ttk.Combobox(self.control_frame, values=self.get_port_list(), width=13, postcommand=self.refresh_port_list)
        self.cbo_port.set(self.reader_thread.port)
        self.cbo_port.pack(side=tk.LEFT, padx=(0, 10))

        # Baud Rate Selector
        tk.Label(self.control_frame, text="Baud:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(5, 2))
        self.cbo_baud = ttk.Combobox(self.control_frame, values=["2400", "4800", "9600", "19200", "38400", "115200"], width=7)
        self.cbo_baud.set("9600")
        self.cbo_baud.pack(side=tk.LEFT, padx=(0, 10))

        # Simulation Mode Checkbox
        self.var_sim = tk.BooleanVar(value=self.reader_thread.simulation_mode)
        self.chk_sim = tk.Checkbutton(
            self.control_frame,
            text="Simulation Mode",
            variable=self.var_sim,
            font=("Segoe UI", 9, "bold"),
            command=self.on_sim_toggle
        )
        self.chk_sim.pack(side=tk.LEFT, padx=10)

        # Test Spike Button (For testing >105 dB alarm instantly)
        self.btn_spike = tk.Button(
            self.control_frame,
            text="⚡ Test >105dB Spike",
            font=("Segoe UI", 9, "bold"),
            bg="#d9534f",
            fg="#ffffff",
            activebackground="#c9302c",
            activeforeground="#ffffff",
            command=self.trigger_alarm_test
        )
        self.btn_spike.pack(side=tk.LEFT, padx=10)

        # Theme Selector
        tk.Label(self.control_frame, text="Theme:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(15, 2))
        self.cbo_theme = ttk.Combobox(self.control_frame, values=list(THEMES.keys()), width=12, state="readonly")
        self.cbo_theme.set(self.theme_name)
        self.cbo_theme.pack(side=tk.LEFT, padx=(0, 5))
        self.cbo_theme.bind("<<ComboboxSelected>>", self.on_theme_change)

        # Apply serial settings button
        self.btn_apply = tk.Button(
            self.control_frame,
            text="Apply Serial",
            font=("Segoe UI", 9, "bold"),
            command=self.apply_serial_settings
        )
        self.btn_apply.pack(side=tk.RIGHT, padx=5)

    def trigger_alarm_test(self):
        """Forces simulation spike to test >105 dB alarm flashing."""
        self.var_sim.set(True)
        self.on_sim_toggle()
        self.reader_thread.trigger_sim_spike()

    def on_sim_toggle(self):
        is_sim = self.var_sim.get()

        if not HAS_SERIAL and not is_sim:
            messagebox.showwarning(
                "pyserial Missing",
                "The 'pyserial' package is not installed in the active Python environment.\n\n"
                "Please run: pip install pyserial\n"
                "Or run using the Python environment where pyserial was installed."
            )
            self.var_sim.set(True)
            return

        port = self.cbo_port.get().strip()
        baud = int(self.cbo_baud.get()) if self.cbo_baud.get().isdigit() else 9600

        self.lbl_mode_badge.config(
            text="SIMULATION MODE" if is_sim else f"CONNECTING ({port})...",
            bg="#337ab7" if is_sim else "#f0ad4e",
            fg="#ffffff"
        )
        self.reader_thread.set_config(
            port=port,
            baudrate=baud,
            simulation_mode=is_sim
        )

    def apply_serial_settings(self):
        port = self.cbo_port.get().strip()
        try:
            baud = int(self.cbo_baud.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid Baud Rate!")
            return
        is_sim = self.var_sim.get()

        self.lbl_mode_badge.config(
            text="SIMULATION MODE" if is_sim else f"CONNECTING ({port})...",
            bg="#337ab7" if is_sim else "#f0ad4e",
            fg="#ffffff"
        )
        self.reader_thread.set_config(port=port, baudrate=baud, simulation_mode=is_sim)

    def on_theme_change(self, event=None):
        self.theme_name = self.cbo_theme.get()
        self.apply_theme()
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
                    val = msg['db']
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
                    else:
                        if self.alarm_active:
                            self.alarm_active = False
                            self.lbl_alarm_badge.config(bg="#222222", fg="#888888", text="ALARM: >105 dB")

                    # Mode & Status Badges
                    if mode == 'SIMULATION':
                        if not self.reader_thread.simulation_mode:
                            continue
                        self.lbl_mode_badge.config(text="SIMULATION MODE", bg="#337ab7", fg="#ffffff")
                        self.lbl_comm_led.config(text="● SIMULATING", fg="#00FF66")
                    else:
                        self.lbl_mode_badge.config(text=f"HARDWARE ({port})", bg="#5cb85c", fg="#ffffff")
                        self.lbl_comm_led.config(text=f"● ONLINE ({port})", fg="#00E5FF")

                    self.redraw_lcd()

                elif msg['status'] in ('ERROR', 'TIMEOUT'):
                    err_msg = msg.get('error', 'Serial Timeout')
                    self.lbl_mode_badge.config(text=f"SERIAL TIMEOUT ({port})", bg="#d9534f", fg="#ffffff")
                    self.lbl_comm_led.config(text=f"● NO RESPONSE", fg="#FF3333")
                    self.stat_labels['val_cur'].config(text="NO DATA")
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
    root.protocol("WM_DELETE_WINDOW", lambda: (app.reader_thread.stop(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
