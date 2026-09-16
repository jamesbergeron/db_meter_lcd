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
- **Theme Dropdown**: Switch between *Matrix Green*, *Retro Amber*, *Cyber Cyan*, and *Crimson Red*.
- **Reset Stats**: Clears the recorded Min, Max, and Average telemetry readouts.

---

## File Structure

```
db_meter_lcd/
├── db_meter_gui.py     # Main GUI application script
├── requirements.txt    # Python dependencies
└── README.md           # Documentation
```
