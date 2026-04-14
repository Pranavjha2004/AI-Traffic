# AI Traffic Detection and Management System

This project is a prototype smart traffic management system that combines:

- an `ESP32-CAM` for live traffic video streaming
- a `FastAPI` backend for AI-based vehicle detection and traffic decision making
- a `React + Vite` dashboard for monitoring and manual controls
- an `ESP8266 NodeMCU` traffic-light controller for LED-based signal simulation

The system is designed for a `+` junction prototype using:

- 1 camera module
- 8 LEDs total
- 4 red LEDs
- 4 green LEDs

## Project architecture

The architecture is intentionally split into four parts:

1. `esp32_cam`
   Streams live MJPEG video from the camera.

2. `Backend`
   Reads the live stream, runs YOLOv8 object detection, estimates congestion, detects emergency vehicles, and produces a signal plan.

3. `Frontend`
   Shows the annotated traffic feed, stream status, traffic metrics, signal recommendation, manual direction controls, and flash toggle.

4. `esp8266_traffic_controller`
   Polls the backend signal plan and drives the traffic LEDs.

## Folder structure

```text
AI Traffic/
├── Backend/
├── Frontend/
├── esp32_cam/
├── esp8266_traffic_controller/
└── README.md
```

## Current workflow

1. The `ESP32-CAM` streams traffic video through `/stream`
2. The backend connects to that stream
3. The backend runs object detection and computes:
   - vehicle count
   - density level
   - congestion score
   - emergency detection
   - recommended signal timing
4. The frontend displays the analyzed output
5. The `ESP8266` controller polls `/signal-plan`
6. The controller updates the 8 LEDs based on the signal recommendation

## Important limitation of this prototype

This prototype uses a single camera.

That means the system does not automatically know all four traffic directions at once.

To support a practical single-camera prototype, the dashboard includes manual direction buttons:

- `Auto`
- `North`
- `East`
- `South`
- `West`

If you manually place the camera toward the `North` road and press `North` on the dashboard:

- the system treats the camera as observing the north approach
- it keeps the north direction red
- it gives green to the perpendicular direction
- it adapts the duration using the observed density in the viewed road

Only one direction can be active at a time.

## Main features

- live MJPEG traffic streaming from ESP32-CAM
- AI vehicle detection using YOLOv8
- ambulance heuristic detection
- density and congestion scoring
- adaptive signal recommendation
- manual observed-direction override
- ESP32-CAM flash control from dashboard
- dashboard with:
  - stream controls
  - flash toggle
  - manual direction buttons
  - annotated live feed
  - traffic statistics
  - vehicle charts
  - recent analyzed frames
- ESP8266 debug traffic controller with detailed serial output

## Software requirements

### On the PC

- Python 3.10+ recommended
- Node.js 18+ recommended
- npm

### Python packages

Backend dependencies are listed in:

- [Backend/requirements.txt](/C:/Users/jhapr/Desktop/AI%20Traffic/Backend/requirements.txt:1)

### Arduino IDE requirements

For `ESP32-CAM`:
- ESP32 board package

For `ESP8266 NodeMCU`:
- ESP8266 board package
- `ArduinoJson` library

## Hardware used

### Camera board

- ESP32-CAM

### Traffic controller board

- ESP8266 NodeMCU

### Junction LEDs

- 4 red LEDs
- 4 green LEDs
- 8 resistors, `220 ohm` to `330 ohm`

## LED wiring for ESP8266 NodeMCU

Use the `ESP8266` traffic controller sketch:

- [esp8266_traffic_controller.ino](/C:/Users/jhapr/Desktop/AI%20Traffic/esp8266_traffic_controller/esp8266_traffic_controller.ino:1)

### Pin mapping

| Road  | Red LED pin | Green LED pin |
|-------|-------------|---------------|
| North | D1          | D2            |
| East  | D5          | D6            |
| South | D7          | D0            |
| West  | D3          | D4            |

### Wiring rule for each LED

1. ESP8266 pin -> resistor -> LED anode (+)
2. LED cathode (-) -> GND

### Warning about ESP8266 pins

`D3` and `D4` are boot-sensitive pins.

This project can work with them for a prototype, but if boot/upload issues appear:

- disconnect LEDs while flashing
- use a slower upload speed
- or move to an external driver like `74HC595` / `PCF8574`

## ESP32-CAM endpoints

After uploading the camera sketch, the board exposes:

- `/stream` -> live MJPEG stream
- `/jpg` -> single snapshot
- `/flash?state=on` -> flashlight ON
- `/flash?state=off` -> flashlight OFF

The camera sketch is:

- [esp32_cam.ino](/C:/Users/jhapr/Desktop/AI%20Traffic/esp32_cam/esp32_cam.ino:1)

## Backend API endpoints

Important backend endpoints:

- `POST /stream/start`
- `POST /stream/stop`
- `GET /stream/status`
- `GET /frames`
- `GET /stats`
- `GET /signal-plan`
- `POST /direction/select`
- `GET /direction/status`
- `POST /camera/flash`
- `GET /camera/flash`
- `GET /health`

## Backend signal-plan behavior

The backend returns a signal plan with values such as:

- `density_level`
- `congestion_score`
- `signal_mode`
- `signal_state`
- `recommended_green_sec`
- `recommended_red_sec`
- `active_direction`
- `preferred_phase`

These values are consumed by:

- the dashboard
- the ESP8266 traffic controller

## How to run the project on another system

### 1. Copy the whole project folder

Copy the entire `AI Traffic` folder to the new machine.

### 2. Backend setup

Open a terminal in:

```powershell
cd Backend
```

Create and activate a virtual environment if desired:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the backend:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000
```

Backend will be available at:

- `http://<PC-IP>:8000`

### 3. Frontend setup

Open another terminal in:

```powershell
cd Frontend
```

Install dependencies:

```powershell
npm install
```

Run the dashboard:

```powershell
npm run dev
```

Open the shown local Vite URL in your browser.

### 4. ESP32-CAM setup

Open:

- [esp32_cam.ino](/C:/Users/jhapr/Desktop/AI%20Traffic/esp32_cam/esp32_cam.ino:1)

Update:

- `ssid`
- `password`

Upload to the ESP32-CAM.

Open Serial Monitor at `115200`.

You should see a line like:

```text
Live stream ready at: http://192.168.x.x/stream
```

### 5. Start stream analysis from dashboard

In the dashboard:

1. Enter the ESP32-CAM stream URL
2. Choose analyze FPS
3. Click `Start live analysis`

### 6. Manual direction selection

Use the direction buttons in the dashboard:

- `Auto`
- `North`
- `East`
- `South`
- `West`

Only one is active at a time.

### 7. Flashlight control

Use the `Enable flash` / `Disable flash` button in the dashboard.

The backend proxies this request to the ESP32-CAM flash endpoint.

### 8. ESP8266 traffic controller setup

Open:

- [esp8266_traffic_controller.ino](/C:/Users/jhapr/Desktop/AI%20Traffic/esp8266_traffic_controller/esp8266_traffic_controller.ino:1)

Update:

- `ssid`
- `password`
- `backendBaseUrl`

Example:

```cpp
const char* backendBaseUrl = "http://192.168.1.100:8000";
```

Upload to the NodeMCU.

Open Serial Monitor at:

- `115200`

## Debugging the ESP8266 controller

The traffic controller sketch is a debug version.

It prints:

- Wi‑Fi connection status
- backend polling URL
- backend JSON response
- active direction
- preferred phase
- signal mode
- current phase
- per-road LED state

Expected readable logs look like:

```text
ESP8266 Traffic Controller Debug Boot
Connecting to WiFi: Vlsi
WiFi connected. Controller IP: 192.168.x.x
Polling backend: http://192.168.1.100:8000/signal-plan
HTTP status: 200
Current backend plan:
  activeDirection: north
  signalMode: manual_direction_override
  signalState: east_west_green
  densityLevel: manual_focus
  preferredPhase: ew_green
  emergencyDetected: false
  greenSeconds: 22
  redSeconds: 38
Begin phase EW_GREEN for 22000 ms
Applying phase: EW_GREEN
```

## How to test the system

### Test backend only

Open in browser:

- `http://<PC-IP>:8000/health`
- `http://<PC-IP>:8000/stream/status`
- `http://<PC-IP>:8000/stats`
- `http://<PC-IP>:8000/signal-plan`

### Test camera stream

Open:

- `http://<ESP32-CAM-IP>/stream`

### Test flash manually

Open:

- `http://<ESP32-CAM-IP>/flash?state=on`
- `http://<ESP32-CAM-IP>/flash?state=off`

### Test dashboard manual direction

1. Start stream analysis
2. Click `North`
3. Confirm `Observed direction` shows `north`
4. Confirm signal recommendation updates

### Test traffic LEDs

1. Connect NodeMCU to Wi‑Fi
2. Confirm backend IP is correct in the sketch
3. Open serial monitor
4. Watch phases change

## Troubleshooting

### 1. Frontend cannot connect to backend

Check:

- backend is running
- same network
- firewall allows port `8000`

### 2. Stream starts but no detections appear

Check:

- camera URL is correct
- ESP32-CAM stream opens in browser
- backend logs show frames being analyzed

### 3. Flash button does not work

Check:

- ESP32-CAM was reflashed with the updated camera sketch
- stream URL is set in the dashboard
- backend can infer the camera host from the stream URL

### 4. ESP8266 serial monitor shows garbage

Set serial monitor to:

- `115200 baud`

Then press reset.

### 5. ESP8266 upload fails

Possible causes:

- bad USB cable
- wrong board selected
- LEDs connected to boot-sensitive pins during upload

For upload reliability:

- disconnect LED wires while flashing
- use upload speed `115200`

### 6. All red LEDs glow

Check:

- backend IP in ESP8266 sketch
- serial output for current phase
- LED polarity
- common GND wiring
- whether the controller is stuck in `ALL_RED`

## Recommended future improvements

- directional ROI counting inside one frame
- multi-camera support for true lane-specific intelligence
- hardware-safe LED drivers
- websocket live updates instead of polling
- yellow signal support
- pedestrian signal handling

## File reference summary

- Backend API: [Backend/main.py](/C:/Users/jhapr/Desktop/AI%20Traffic/Backend/main.py:1)
- Backend deps: [Backend/requirements.txt](/C:/Users/jhapr/Desktop/AI%20Traffic/Backend/requirements.txt:1)
- Frontend dashboard: [Frontend/src/App.jsx](/C:/Users/jhapr/Desktop/AI%20Traffic/Frontend/src/App.jsx:1)
- Frontend styles: [Frontend/src/App.css](/C:/Users/jhapr/Desktop/AI%20Traffic/Frontend/src/App.css:1)
- ESP32-CAM sketch: [esp32_cam/esp32_cam.ino](/C:/Users/jhapr/Desktop/AI%20Traffic/esp32_cam/esp32_cam.ino:1)
- ESP8266 controller sketch: [esp8266_traffic_controller/esp8266_traffic_controller.ino](/C:/Users/jhapr/Desktop/AI%20Traffic/esp8266_traffic_controller/esp8266_traffic_controller.ino:1)
