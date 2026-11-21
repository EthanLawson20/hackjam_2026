// flood sensor unit. wires in the bucket, and i tell the station how high the water is
#include <Arduino.h>
#include <WiFiS3.h>

// the station's own network (same as AP_SSID and AP_PASS in its config.h)
const char ssid[] = "FloodAir";
const char pass[] = "floodair123";
const IPAddress station(192, 168, 4, 1);

// one wire from GND sits at the bottom of the bucket. these two end higher up
const int PIN_WARN = 2;     // wire ending at the warning height
const int PIN_DANGER = 3;   // wire ending at the danger height

// what i report for each step, as a percent of the gauge
const int LEVEL_DRY = 0;
const int LEVEL_WARN = 50;
const int LEVEL_DANGER = 85;

const unsigned long SEND_EVERY_MS = 5000;   // the station gives up on me after 30s of silence

int level = LEVEL_DRY;
unsigned long lastSend = 0;

void joinWifi() {
  while (WiFi.begin(ssid, pass) != WL_CONNECTED) {
    Serial.println("looking for FloodAir...");
    delay(3000);
  }
  // the ip reads 0.0.0.0 for a moment after connecting, so i wait it out
  while (WiFi.localIP() == IPAddress(0, 0, 0, 0)) delay(100);
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
  WiFiClient client;
  if (!client.connect(station, 80)) {
    Serial.println("station didn't answer");
    return;
  }
  client.print("GET /api/update?water=");
  client.print(level);
  client.println(" HTTP/1.1");
  client.println("Host: 192.168.4.1");
  client.println("Connection: close");
  client.println();

  // let the station finish its reply before i hang up
  unsigned long start = millis();
  while (client.connected() && millis() - start < 1500) {
    while (client.available()) client.read();
  }
  client.stop();

  Serial.print("sent water=");
  Serial.println(level);
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  // pulled up means a dry wire reads HIGH, and water to ground pulls it LOW
  pinMode(PIN_WARN, INPUT_PULLUP);
  pinMode(PIN_DANGER, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);

  if (WiFi.status() == WL_NO_MODULE) {
    Serial.println("can't talk to the wifi module");
    while (true) {}
  }
  joinWifi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) joinWifi();

  int now = readLevel();
  digitalWrite(LED_BUILTIN, now != LEVEL_DRY);   // light on = water on a wire

  // i send right away when the level changes, otherwise every few seconds
  if (now != level || millis() - lastSend >= SEND_EVERY_MS) {
    level = now;
    sendLevel();
    lastSend = millis();
  }
  delay(100);
}