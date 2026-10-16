# Graphical LCD 7-Segment Sound Level Meter (Modbus RTU)

A cross-platform (Windows & Linux) graphical sound level meter application with a vector 7-segment digital display, dynamic 105+ dB red flashing alarm, bar graph meter, peak hold indicator, and telemetry statistics.

![LCD Meter Screenshot](../../brain/cc59ed65-0adc-432d-b726-b37c72359249/lcd_db_meter_mockup_1789597975060.png)

---

## Features

- **Vector 7-Segment LCD Display**: Authentic digital segment geometry with customizable themes (Matrix Green, Retro Amber, Cyber Cyan, Crimson Red) and inactive segment ghosting.
- **Flashing Alarm System (> 105.0 dB)**: When decibel levels exceed 105 dB, the panel background, bezel, and display pulse in intense emergency red with a prominent warning banner overlay.
- **Cross-Platform Serial Modbus RTU Reader**: Compatible with Windows (`COM1`, `COM3`, etc.) and Linux (`/dev/ttyACM0`, `/dev/ttyUSB0`, etc.). Uses standard Modbus RTU CRC16 frame validation (`[SLAVE_ADDR, 0x03, 0x00, 0x00, 0x00, 0x01]`).
- **Built-in Simulation Mode**: Included toggle switch allows testing full visual displays, bar graphs, and 105+ dB alarm flashing without needing physical hardware connected.
- **Bar Graph & Peak Hold**: 35-segment LED intensity bar (30 dB to 110 dB) with decaying white peak hold marker.
- **Telemetry Readouts**: Tracks MIN dB, MAX dB, AVERAGE dB, and CURRENT dB in real-time.

---

## Quick Start Guide

### 1. Requirements & Dependencies

Make sure Python 3.8+ is installed on your system. Install `pyserial`:

```bash
pip install -r requirements.txt
```

*(Note: `tkinter` comes pre-installed with standard Python distributions on Windows and most Linux distros. On Debian/Ubuntu Linux, if missing, run `sudo apt install python3-tk`.)*

### 2. Launching the Application

Run the Python script directly:

#### On Windows:
```cmd
python db_meter_gui.py
```

#### On Linux:
```bash
python3 db_meter_gui.py
```

---

## Serial Port Setup & Permissions

### Linux Configuration:
1. Plug in your Modbus sound sensor (typically creates `/dev/ttyACM0` or `/dev/ttyUSB0`).
2. Give your user account permission to access serial ports:
   ```bash
   sudo usermod -a -G dialout $USER
   ```
   *(Re-login or reboot for group permissions to take effect).*
3. Select your port in the GUI dropdown list and click **Apply Serial**.

### Windows Configuration:
1. Open Device Manager to identify your sensor's assigned COM port (e.g., `COM3`).
2. Select `COM3` from the port dropdown list, choose Baud Rate (`9600`), and uncheck **Simulation Mode** (or click **Apply Serial**).

---

## Controls & Customization

- **Simulation Mode Checkbox**: Toggle to test the app with realistic randomized sound level simulation.
- **⚡ Test >105dB Spike**: Instantly triggers a high-decibel acoustic spike in simulation mode to verify the red flashing alarm visual effects.
- **💬 Discord Alerts**: Opens Discord Webhook configuration popup to send automated instant notifications to a Discord channel or user when decibels exceed 105 dB.
- **Theme Dropdown**: Switch between *Matrix Green*, *Retro Amber*, *Cyber Cyan*, and *Crimson Red*.
- **Reset Stats**: Clears the recorded Min, Max, and Average telemetry readouts.

---

## 💬 Setting Up Discord Webhook Notifications

1. Open Discord, go to **Server Settings** → **Integrations** → **Webhooks** → **Create Webhook**.
2. Copy the generated Webhook URL.
3. In the Sound Level Meter GUI, click the **💬 Discord Alerts** button.
4. Check **Enable Discord Notifications**.
5. Paste your **Webhook URL**.
6. (Optional) Paste your Discord User ID (Right-click your profile in Discord → *Copy User ID*) to get pinged `<@USER_ID>` whenever an alarm occurs.
7. Click **🧪 Send Test Alert** to test, then click **Save & Close**.

---

## Network sender (Raspberry Pi)

`db_meter_sender.py` reads the Modbus sensor over USB/RS485 and streams JSON on the LAN:

- **UDP broadcast** — port `55123` (auto-discovery style)
- **TCP server** — port `55124` (direct connections; used by the Web UI bridge)

See `db_meter_sender.py --help` for serial port and interval options.

---

## Web UI (iPhone & Android on the same Wi‑Fi)

`db_meter_bridge.py` serves a mobile-friendly dashboard and sends **over-threshold** alerts via **Discord** and/or **ntfy** (phone push even when the browser is closed).

### On the Pi

1. Install dependencies (includes Flask):

   ```bash
   pip3 install -r requirements.txt
   ```

2. Start the sender (sensor attached):

   ```bash
   python3 db_meter_sender.py
   ```

3. Start the bridge (same machine; default TCP `127.0.0.1:55124`):

   ```bash
   python3 db_meter_bridge.py
   ```

4. On your phone, open `http://<pi-ip>:8080/` (default HTTP port from `web_config.json`).

### Configuration

Default settings live in **`web_config.json`** in this repo (copied to the Pi with the project). You can edit that file directly or use **Settings** in the Web UI — **Save settings** writes back to `web_config.json` on the Pi.

| Setting | Purpose |
|--------|---------|
| `tcp_host` / `tcp_port` | Where the bridge connects to `db_meter_sender` (`127.0.0.1:55124` on the Pi) |
| `alarm_threshold` / `alarm_cooldown_seconds` | When to alert and minimum time between alerts |
| `discord_*` | Same webhook style as the desktop GUI |
| `ntfy_*` | Subscribe to `ntfy_topic` in the [ntfy app](https://ntfy.sh/) (`ntfy_server` default `https://ntfy.sh`) |
| `http_port` / `http_bind` | Web server listen address (restart bridge after changing these) |
| `calibration_enabled` / `calibration_points` | Same format as `config.json` for the desktop GUI |

`web_config.json` in the repo is pre-filled to match the usual desktop `config.json` values (alarm, Discord, calibration, TCP). Desktop-only keys (`port`, `baudrate`, `theme`, `source_mode`, `udp_listen_port`, etc.) remain in **`config.json`** for `db_meter_gui.py` only.

**Pi tip:** If `db_meter_sender.py` and `db_meter_bridge.py` both run on the Pi, set `tcp_host` to **`127.0.0.1`** instead of the Pi’s LAN IP.

**Security:** The Web UI has no login; use only on a trusted home LAN. Webhooks and ntfy topics are stored in plain JSON on the Pi.

### ntfy quick setup

1. Install the ntfy app on your phone.
2. Subscribe to a private topic name (e.g. `my-garage-meter-8f3k`).
3. Enter that topic in Web UI → Settings → ntfy → **Test ntfy** → enable alerts → **Save settings**.

---

## File Structure

```
db_meter_lcd/
├── db_meter_gui.py       # Desktop GUI (Windows / Linux)
├── db_meter_sender.py    # Pi / headless Modbus → UDP + TCP
├── db_meter_bridge.py    # Web UI + Discord / ntfy alerts
├── web_config.json       # Default bridge settings (editable in Web UI)
├── web/                  # Static Web UI (HTML / CSS / JS)
├── requirements.txt      # Python dependencies
└── README.md             # Documentation
```
