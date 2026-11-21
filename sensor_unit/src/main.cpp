// flood sensor unit. wires in the bucket, and i tell the station how high the water is
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>

// the station's own network (same as AP_SSID and AP_PASS in its config.h)
const char ssid[] = "FloodAir";
const char pass[] = "floodair123";
const char station[] = "http://192.168.4.1/api/update?water=";

// one wire from GND sits at the bottom of the bucket. these two end higher up
const int PIN_WARN = 4;     // wire ending at the warning height
const int PIN_DANGER = 5;   // wire ending at the danger height
// what i report for each step, as a percent of the gauge
const int LEVEL_DRY = 0;
const int LEVEL_WARN = 50;
const int LEVEL_DANGER = 85;

const unsigned long SEND_EVERY_MS = 5000;   // the station gives up on me after 30s of silence

int level = LEVEL_DRY;
unsigned long lastSend = 0;

void joinWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, pass);
  while (WiFi.status() != WL_CONNECTED) {
    Serial.println("looking for FloodAir...");
    delay(1000);
  }
  Serial.println("joined FloodAir");
}

// a wire only counts as wet if it stays wet for a moment, so splashes don't trip it
bool wet(int pin) {
  for (int i = 0; i < 5; i++) {
    if (digitalRead(pin) == HIGH) return false;
    delay(10);
  }
  return true;
}

int readLevel() {
  if (wet(PIN_DANGER)) return LEVEL_DANGER;
  if (wet(PIN_WARN)) return LEVEL_WARN;
  return LEVEL_DRY;
}

void sendLevel() {
  HTTPClient http;
  http.setTimeout(2000);
  http.begin(String(station) + level);
  int code = http.GET();
  http.end();

  if (code == 200) {
    Serial.print("sent water=");
    Serial.println(level);
  } else {
    Serial.print("station didn't take it, code ");
    Serial.println(code);
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // pulled up means a dry wire reads HIGH, and water to ground pulls it LOW
  pinMode(PIN_WARN, INPUT_PULLUP);
  pinMode(PIN_DANGER, INPUT_PULLUP);

  joinWifi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) joinWifi();

  int now = readLevel();

  // i send right away when the level changes, otherwise every few seconds
  if (now != level || millis() - lastSend >= SEND_EVERY_MS) {
    level = now;
    sendLevel();
    lastSend = millis();
  }
  delay(100);
}