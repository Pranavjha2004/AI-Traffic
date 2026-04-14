#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>

const char* ssid = "Vlsi";
const char* password = "vlsi80211";
const char* backendBaseUrl = "http://10.186.164.1:8000";

const unsigned long backendPollMs = 2500;
const unsigned long allRedMs = 1000;
const unsigned long debugPrintMs = 2000;

const int NORTH_RED_PIN = D1;
const int NORTH_GREEN_PIN = D2;
const int EAST_RED_PIN = D5;
const int EAST_GREEN_PIN = D6;
const int SOUTH_RED_PIN = D7;
const int SOUTH_GREEN_PIN = D0;
const int WEST_RED_PIN = D3;
const int WEST_GREEN_PIN = D4;

enum TrafficPhase {
  PHASE_NS_GREEN,
  PHASE_EW_GREEN,
  PHASE_ALL_RED
};

struct BackendPlan {
  bool valid = false;
  bool emergencyDetected = false;
  String signalMode = "idle";
  String signalState = "unknown";
  String densityLevel = "low";
  String activeDirection = "auto";
  String preferredPhase = "";
  int greenSeconds = 15;
  int redSeconds = 45;
};

BackendPlan currentPlan;
TrafficPhase currentPhase = PHASE_NS_GREEN;
TrafficPhase nextPhaseAfterAllRed = PHASE_EW_GREEN;

unsigned long phaseStartedAt = 0;
unsigned long phaseDurationMs = 15000;
unsigned long lastBackendPollAt = 0;
unsigned long lastDebugPrintAt = 0;

void configurePin(int pin) {
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);
}

const char* phaseName(TrafficPhase phase) {
  if (phase == PHASE_NS_GREEN) return "NS_GREEN";
  if (phase == PHASE_EW_GREEN) return "EW_GREEN";
  return "ALL_RED";
}

TrafficPhase phaseFromPreferred(const String& preferredPhase) {
  if (preferredPhase == "ew_green") return PHASE_EW_GREEN;
  if (preferredPhase == "ns_green") return PHASE_NS_GREEN;
  return PHASE_NS_GREEN;
}

void printLedState(const char* road, bool redOn, bool greenOn) {
  Serial.print(road);
  Serial.print(" => RED=");
  Serial.print(redOn ? "ON" : "OFF");
  Serial.print(" GREEN=");
  Serial.println(greenOn ? "ON" : "OFF");
}

void setRoadLights(const char* road, int redPin, int greenPin, bool redOn, bool greenOn) {
  digitalWrite(redPin, redOn ? HIGH : LOW);
  digitalWrite(greenPin, greenOn ? HIGH : LOW);
  printLedState(road, redOn, greenOn);
}

void applyPhase(TrafficPhase phase) {
  Serial.print("Applying phase: ");
  Serial.println(phaseName(phase));

  if (phase == PHASE_NS_GREEN) {
    setRoadLights("NORTH", NORTH_RED_PIN, NORTH_GREEN_PIN, false, true);
    setRoadLights("SOUTH", SOUTH_RED_PIN, SOUTH_GREEN_PIN, false, true);
    setRoadLights("EAST", EAST_RED_PIN, EAST_GREEN_PIN, true, false);
    setRoadLights("WEST", WEST_RED_PIN, WEST_GREEN_PIN, true, false);
  } else if (phase == PHASE_EW_GREEN) {
    setRoadLights("NORTH", NORTH_RED_PIN, NORTH_GREEN_PIN, true, false);
    setRoadLights("SOUTH", SOUTH_RED_PIN, SOUTH_GREEN_PIN, true, false);
    setRoadLights("EAST", EAST_RED_PIN, EAST_GREEN_PIN, false, true);
    setRoadLights("WEST", WEST_RED_PIN, WEST_GREEN_PIN, false, true);
  } else {
    setRoadLights("NORTH", NORTH_RED_PIN, NORTH_GREEN_PIN, true, false);
    setRoadLights("SOUTH", SOUTH_RED_PIN, SOUTH_GREEN_PIN, true, false);
    setRoadLights("EAST", EAST_RED_PIN, EAST_GREEN_PIN, true, false);
    setRoadLights("WEST", WEST_RED_PIN, WEST_GREEN_PIN, true, false);
  }
}

void beginPhase(TrafficPhase phase, unsigned long durationMs) {
  currentPhase = phase;
  phaseStartedAt = millis();
  phaseDurationMs = durationMs;
  Serial.print("Begin phase ");
  Serial.print(phaseName(phase));
  Serial.print(" for ");
  Serial.print(durationMs);
  Serial.println(" ms");
  applyPhase(phase);
}

String endpointUrl(const char* path) {
  return String(backendBaseUrl) + path;
}

bool fetchBackendPlan(BackendPlan& outPlan) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected while polling backend.");
    return false;
  }

  WiFiClient client;
  HTTPClient http;
  String url = endpointUrl("/signal-plan");

  Serial.print("Polling backend: ");
  Serial.println(url);

  if (!http.begin(client, url)) {
    Serial.println("HTTP begin failed.");
    return false;
  }

  http.setTimeout(3000);
  int statusCode = http.GET();
  if (statusCode <= 0) {
    Serial.print("HTTP GET failed with code: ");
    Serial.println(statusCode);
    http.end();
    return false;
  }

  Serial.print("HTTP status: ");
  Serial.println(statusCode);
  if (statusCode != 200) {
    http.end();
    return false;
  }

  String body = http.getString();
  Serial.println("Backend response:");
  Serial.println(body);
  http.end();

  DynamicJsonDocument doc(1536);
  DeserializationError error = deserializeJson(doc, body);
  if (error) {
    Serial.print("JSON parse error: ");
    Serial.println(error.c_str());
    return false;
  }

  if (doc["recommended_green_sec"].isNull()) {
    Serial.println("No recommended_green_sec yet.");
    return false;
  }

  outPlan.valid = true;
  outPlan.emergencyDetected = doc["emergency_detected"] | false;
  outPlan.signalMode = String((const char*)(doc["signal_mode"] | "idle"));
  outPlan.signalState = String((const char*)(doc["signal_state"] | "unknown"));
  outPlan.densityLevel = String((const char*)(doc["density_level"] | "low"));
  outPlan.activeDirection = String((const char*)(doc["active_direction"] | "auto"));
  outPlan.preferredPhase = String((const char*)(doc["preferred_phase"] | ""));
  outPlan.greenSeconds = doc["recommended_green_sec"] | 15;
  outPlan.redSeconds = doc["recommended_red_sec"] | 45;
  return true;
}

void printCurrentPlan() {
  Serial.println("Current backend plan:");
  Serial.print("  activeDirection: ");
  Serial.println(currentPlan.activeDirection);
  Serial.print("  signalMode: ");
  Serial.println(currentPlan.signalMode);
  Serial.print("  signalState: ");
  Serial.println(currentPlan.signalState);
  Serial.print("  densityLevel: ");
  Serial.println(currentPlan.densityLevel);
  Serial.print("  preferredPhase: ");
  Serial.println(currentPlan.preferredPhase);
  Serial.print("  emergencyDetected: ");
  Serial.println(currentPlan.emergencyDetected ? "true" : "false");
  Serial.print("  greenSeconds: ");
  Serial.println(currentPlan.greenSeconds);
  Serial.print("  redSeconds: ");
  Serial.println(currentPlan.redSeconds);
}

void applyPlanUpdate() {
  BackendPlan nextPlan;
  if (!fetchBackendPlan(nextPlan)) {
    Serial.println("Backend plan update skipped.");
    return;
  }

  currentPlan = nextPlan;
  printCurrentPlan();
}

void updateTrafficStateMachine() {
  unsigned long now = millis();
  unsigned long targetGreenMs = (unsigned long)currentPlan.greenSeconds * 1000UL;

  if (currentPlan.signalMode == "manual_direction_override" && currentPlan.preferredPhase.length() > 0) {
    TrafficPhase preferred = phaseFromPreferred(currentPlan.preferredPhase);

    if (currentPhase == PHASE_ALL_RED) {
      if (now - phaseStartedAt >= phaseDurationMs) {
        beginPhase(preferred, targetGreenMs);
      }
      return;
    }

    if (currentPhase != preferred) {
      nextPhaseAfterAllRed = preferred;
      beginPhase(PHASE_ALL_RED, allRedMs);
      return;
    }

    phaseDurationMs = targetGreenMs;
    return;
  }

  if (currentPlan.emergencyDetected && currentPlan.signalMode == "emergency_override") {
    if (currentPhase == PHASE_NS_GREEN) {
      phaseDurationMs = targetGreenMs;
    } else {
      nextPhaseAfterAllRed = PHASE_NS_GREEN;
      beginPhase(PHASE_ALL_RED, allRedMs);
    }
    return;
  }

  if (currentPhase == PHASE_ALL_RED) {
    if (now - phaseStartedAt >= phaseDurationMs) {
      beginPhase(nextPhaseAfterAllRed, targetGreenMs);
    }
    return;
  }

  if (now - phaseStartedAt >= phaseDurationMs) {
    nextPhaseAfterAllRed = (currentPhase == PHASE_NS_GREEN) ? PHASE_EW_GREEN : PHASE_NS_GREEN;
    beginPhase(PHASE_ALL_RED, allRedMs);
  } else {
    phaseDurationMs = targetGreenMs;
  }
}

void connectWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("WiFi connected. Controller IP: ");
  Serial.println(WiFi.localIP());
}

void setupPins() {
  configurePin(NORTH_RED_PIN);
  configurePin(NORTH_GREEN_PIN);
  configurePin(EAST_RED_PIN);
  configurePin(EAST_GREEN_PIN);
  configurePin(SOUTH_RED_PIN);
  configurePin(SOUTH_GREEN_PIN);
  configurePin(WEST_RED_PIN);
  configurePin(WEST_GREEN_PIN);
  beginPhase(PHASE_ALL_RED, allRedMs);
}

void printHeartbeat() {
  Serial.println("Heartbeat:");
  Serial.print("  currentPhase: ");
  Serial.println(phaseName(currentPhase));
  Serial.print("  phaseDurationMs: ");
  Serial.println(phaseDurationMs);
  Serial.print("  millisInPhase: ");
  Serial.println(millis() - phaseStartedAt);
}

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("ESP8266 Traffic Controller Debug Boot");
  setupPins();
  connectWiFi();

  currentPlan.valid = true;
  currentPlan.greenSeconds = 15;
  currentPlan.redSeconds = 45;
  currentPlan.activeDirection = "auto";

  applyPlanUpdate();
  beginPhase(PHASE_NS_GREEN, (unsigned long)currentPlan.greenSeconds * 1000UL);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  unsigned long now = millis();
  if (now - lastBackendPollAt >= backendPollMs) {
    lastBackendPollAt = now;
    applyPlanUpdate();
  }

  if (now - lastDebugPrintAt >= debugPrintMs) {
    lastDebugPrintAt = now;
    printHeartbeat();
  }

  updateTrafficStateMachine();
  delay(20);
}
