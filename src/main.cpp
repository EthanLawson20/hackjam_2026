// flood + air warning station
// the board runs the website and the touchscreen. sensors push numbers to /api/update
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include "config.h"
#include "lgfx_cyd.h"
#include "web_page.h"

LGFX tft;
WebServer server(80);

// short names for the font and text-anchor stuff
namespace F = lgfx::fonts;
typedef lgfx::textdatum_t Datum;

// levels. plain ints so they drop straight into json
const int LV_NONE = -1, LV_OK = 0, LV_WARN = 1, LV_DANGER = 2;

#define C565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))
const uint16_t C_BG      = C565(10, 24, 32);
const uint16_t C_PANEL   = C565(22, 46, 60);
const uint16_t C_TEXT    = C565(240, 246, 248);
const uint16_t C_DIM     = C565(140, 162, 172);
const uint16_t C_INK     = C565(8, 20, 26);
const uint16_t C_OK      = C565(46, 190, 110);
const uint16_t C_WARN    = C565(245, 176, 20);
const uint16_t C_DANGER  = C565(226, 60, 50);
const uint16_t C_DANGER2 = C565(130, 28, 24);
const uint16_t C_NONE    = C565(96, 116, 126);

struct Sensor {
    const char *key;      // name in the json
    const char *title;    // on the screen
    const char *suffix;   // glued onto the number
    const char *scale;    // what the number means
    float warnAt, dangerAt, maxVal;
    const char *tips[4][2];   // two short lines per level, index = level + 1
    float value;
    uint32_t lastSeen;
    bool everSeen;
    int lastLevel;
    long drawnKey;
    int predicted;   // ai score 0-100, -1 = none
};

Sensor flood = {
    "flood", "FLOOD", "%", "percent of sensor range",
    WATER_WARN, WATER_DANGER, WATER_MAX,
    {{"No reading from the water", "sensor right now."},
     {"Water level is normal.", ""},
     {"Water is rising. Move valuables", "up and avoid low roads."},
     {"Flooding likely. Get to higher", "ground, stay out of the water."}},
    0, 0, false, LV_NONE, -1, -1};

Sensor air = {
    "air", "AIR QUALITY", "", "air quality index (AQI)",
    AQI_WARN, AQI_DANGER, AQI_MAX,
    {{"No reading from the air", "sensor right now."},
     {"Air quality is fine.", ""},
     {"Sensitive groups should limit", "time outside."},
     {"Unhealthy air. Stay inside,", "close windows, mask up outside."}},
    0, 0, false, LV_NONE, -1, -1};

// fake readings so i can show every state without dunking the sensor
struct Scene { float water, aqi; };
const Scene SCENES[] = {{12, 38}, {55, 62}, {20, 172}, {85, 210}};
const int N_SCENES = sizeof(SCENES) / sizeof(SCENES[0]);
int demo = 0;   // 0 = live sensors, 1..N = a fake scene

// short history of level changes for the website
struct Event { const char *what; int level; uint32_t at; };
const int LOG_SIZE = 8;
Event events[LOG_SIZE];
int eventCount = 0;

String address;    // what people type into a browser
String footNote;
uint32_t predictedAt = 0;   // when the model last answered
bool havePrediction = false;
Sensor *detail = nullptr;   // null = home screen
bool redrawAll = true;

// ---------- readings ----------

float valueOf(const Sensor &s) {
    if (demo) return (&s == &flood) ? SCENES[demo - 1].water : SCENES[demo - 1].aqi;
    return s.value;
}

bool fresh(const Sensor &s) {
    return demo || (s.everSeen && millis() - s.lastSeen < STALE_AFTER_MS);
}

int levelOf(const Sensor &s) {
    if (!fresh(s)) return LV_NONE;
    float v = valueOf(s);
    if (v >= s.dangerAt) return LV_DANGER;
    if (v >= s.warnAt) return LV_WARN;
    return LV_OK;
}

// -1 when the model hasn't answered lately
int predictionOf(const Sensor &s) {
    if (!havePrediction || millis() - predictedAt > 3UL * PREDICT_EVERY_MS) return -1;
    return s.predicted;
}

void logEvent(const char *what, int level) {
    for (int i = LOG_SIZE - 1; i > 0; i--) events[i] = events[i - 1];
    events[0] = {what, level, millis()};
    if (eventCount < LOG_SIZE) eventCount++;
}

void trackLevels() {
    Sensor *all[] = {&flood, &air};
    for (Sensor *s : all) {
        int lv = levelOf(*s);
        if (lv == s->lastLevel) continue;
        // i only log alerts and the all-clear after one, not every boot-up "ok"
        if (lv >= LV_WARN || (lv == LV_OK && s->lastLevel >= LV_WARN)) logEvent(s->key, lv);
        s->lastLevel = lv;
    }
}

// ---------- web ----------

String sensorJson(const Sensor &s) {
    int lv = levelOf(s);
    String j = "{\"level\":";
    j += lv;
    j += ",\"value\":";
    if (lv == LV_NONE) j += "null"; else j += String(valueOf(s), 1);
    j += ",\"age\":";
    if (demo || !s.everSeen) j += "null"; else j += (unsigned long)((millis() - s.lastSeen) / 1000);
    j += ",\"predicted\":";
    if (predictionOf(s) < 0) j += "null"; else j += predictionOf(s);
    j += ",\"suffix\":\""; j += s.suffix;
    j += "\",\"warnAt\":"; j += String(s.warnAt, 0);
    j += ",\"dangerAt\":"; j += String(s.dangerAt, 0);
    j += ",\"max\":"; j += String(s.maxVal, 0);
    j += "}";
    return j;
}

void handleRoot() {
    server.send_P(200, "text/html", PAGE_HTML);
}

void handleStatus() {
    String j = "{\"demo\":";
    j += demo ? "true" : "false";
    j += ",\"uptime\":"; j += (unsigned long)(millis() / 1000);
    j += ",\"staleAfter\":"; j += (unsigned long)(STALE_AFTER_MS / 1000);
    j += ",\"internet\":"; j += (WiFi.status() == WL_CONNECTED) ? "true" : "false";
    j += ",\"flood\":"; j += sensorJson(flood);
    j += ",\"air\":"; j += sensorJson(air);
    j += ",\"log\":[";
    for (int i = 0; i < eventCount; i++) {
        if (i) j += ",";
        j += "{\"what\":\""; j += events[i].what;
        j += "\",\"level\":"; j += events[i].level;
        j += ",\"ago\":"; j += (unsigned long)((millis() - events[i].at) / 1000);
        j += "}";
    }
    j += "]}";
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "application/json", j);
}

// sensors hit this: /api/update?water=55&aqi=120  (either one or both)
void handleUpdate() {
    bool got = false;
    if (server.hasArg("water")) {
        flood.value = server.arg("water").toFloat();
        flood.lastSeen = millis(); flood.everSeen = true; got = true;
    }
    if (server.hasArg("aqi")) {
        air.value = server.arg("aqi").toFloat();
        air.lastSeen = millis(); air.everSeen = true; got = true;
    }
    if (got) server.send(200, "text/plain", "ok");
    else server.send(400, "text/plain", "send water= and/or aqi=");
}

// /api/demo?scene=0..4 does the same thing as the DEMO button
void handleDemo() {
    if (server.hasArg("scene")) demo = constrain((int)server.arg("scene").toInt(), 0, N_SCENES);
    server.send(200, "text/plain", String(demo));
}

// ---------- ai scores ----------

// pulls one number out of the reply, -1 if it's missing or null
int scoreFrom(const String &body, const char *key) {
    String tag = String("\"") + key + "\":";
    int i = body.indexOf(tag);
    if (i < 0) return -1;
    String rest = body.substring(i + tag.length());
    rest.trim();
    if (!isDigit(rest[0])) return -1;
    int score = (int)lroundf(rest.toFloat());
    return constrain(score, 0, 100);
}

// runs on its own so a slow reply never freezes the screen or the website
void predictTask(void *) {
    // i give the hotspot 20s. if it isn't there i stop looking so FloodAir stays steady
    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) delay(250);
    if (WiFi.status() != WL_CONNECTED) {
        WiFi.setAutoReconnect(false);
        WiFi.disconnect();
        Serial.println("no hotspot, ai scores are off until i restart");
        vTaskDelete(nullptr);
    }
    Serial.println("hotspot joined, asking the model for scores");

    for (;;) {
        if (WiFi.status() == WL_CONNECTED) {
            String url = PREDICT_URL "?";
            if (fresh(flood)) { url += "water="; url += String(valueOf(flood), 1); url += "&"; }
            if (fresh(air)) { url += "aqi="; url += String(valueOf(air), 1); }

            WiFiClientSecure secure;
            secure.setInsecure();   // not checking the certificate, fine for a demo
            WiFiClient plain;
            HTTPClient http;
            http.setTimeout(8000);
            bool ok = url.startsWith("https") ? http.begin(secure, url) : http.begin(plain, url);
            if (ok) {
                int code = http.GET();
                if (code == 200) {
                    String body = http.getString();
                    flood.predicted = scoreFrom(body, "flood");
                    air.predicted = scoreFrom(body, "air");
                    predictedAt = millis();
                    havePrediction = true;
                } else {
                    Serial.printf("model replied %d\n", code);
                }
                http.end();
            }
        }
        delay(PREDICT_EVERY_MS);
    }
}

void startNetwork() {
    // i'm always the network. the hotspot is only my way out to the internet
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(AP_SSID, AP_PASS, 1, 0, 10);   // let up to 10 devices join
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    address = WiFi.softAPIP().toString();
    footNote = "join wifi " AP_SSID " then open:";
    MDNS.begin(HOSTNAME);
    Serial.print("open http://");
    Serial.println(address);

    server.on("/", handleRoot);
    server.on("/api/status", handleStatus);
    server.on("/api/update", handleUpdate);
    server.on("/api/demo", handleDemo);
    server.begin();

    xTaskCreatePinnedToCore(predictTask, "predict", 12288, nullptr, 1, nullptr, 0);
}

// ---------- screen ----------

uint16_t colorFor(int lv) {
    if (lv == LV_DANGER) return C_DANGER;
    if (lv == LV_WARN) return C_WARN;
    if (lv == LV_OK) return C_OK;
    return C_NONE;
}

const char *wordFor(int lv) {
    if (lv == LV_DANGER) return "DANGER";
    if (lv == LV_WARN) return "WARNING";
    if (lv == LV_OK) return "OK";
    return "NO DATA";
}

// dark text reads better on green and amber, white on red and grey
uint16_t textOn(int lv) {
    return (lv == LV_OK || lv == LV_WARN) ? C_INK : C_TEXT;
}

String shownValue(const Sensor &s) {
    if (levelOf(s) == LV_NONE) return String("--");
    String v = String((int)lroundf(valueOf(s)));
    v += s.suffix;
    return v;
}

String aiLine(const Sensor &s) {
    int p = predictionOf(s);
    String t = AI_LABEL " ";
    if (p < 0) t += "--"; else { t += p; t += "%"; }
    return t;
}

void label(const char *txt, int x, int y, const lgfx::IFont *font, uint16_t color, Datum datum) {
    tft.setFont(font);
    tft.setTextColor(color);
    tft.setTextDatum(datum);
    tft.drawString(txt, x, y);
}

void drawBanner(bool force) {
    static int lastKey = -1;
    int lvF = levelOf(flood), lvA = levelOf(air);
    int top = max(lvF, lvA);
    bool dim = top == LV_DANGER && (millis() / 500) % 2;   // blink when it's bad
    int key = (top + 1) * 100 + (lvF + 1) * 10 + (lvA + 1) + (dim ? 1000 : 0);
    if (!force && key == lastKey) return;
    lastKey = key;

    String msg;
    if (top == LV_NONE) msg = "NO SENSOR DATA";
    else if (top == LV_OK) msg = "ALL CLEAR";
    else {
        msg = wordFor(top);
        msg += ": ";
        msg += (lvF == top && lvA == top) ? "FLOOD + AIR" : (lvF == top) ? "FLOOD" : "AIR";
    }
    tft.fillRect(0, 0, 320, 46, dim ? C_DANGER2 : colorFor(top));
    tft.setFont(&F::FreeSansBold12pt7b);
    if (tft.textWidth(msg.c_str()) > 300) tft.setFont(&F::FreeSansBold9pt7b);
    tft.setTextColor(textOn(top));
    tft.setTextDatum(Datum::middle_center);
    tft.drawString(msg.c_str(), 160, 23);
}

void drawCard(Sensor &s, int x, bool force) {
    int lv = levelOf(s);
    long key = ((lv + 1) * 100000L + (lv == LV_NONE ? 0 : lroundf(valueOf(s)))) * 128 + (predictionOf(s) + 1);
    if (!force && key == s.drawnKey) return;
    s.drawnKey = key;

    tft.fillRoundRect(x, 54, 148, 134, 8, colorFor(lv));
    tft.fillRoundRect(x + 3, 57, 142, 128, 6, C_PANEL);
    label(s.title, x + 74, 74, &F::FreeSansBold9pt7b, C_DIM, Datum::middle_center);
    label(shownValue(s).c_str(), x + 74, 112, &F::FreeSansBold24pt7b, C_TEXT, Datum::middle_center);
    label(wordFor(lv), x + 74, 146, &F::FreeSansBold9pt7b, colorFor(lv), Datum::middle_center);
    label(aiLine(s).c_str(), x + 74, 170, &F::Font2, C_TEXT, Datum::middle_center);
}

void drawFooter(bool force) {
    static String lastAddr;
    static int lastDemo = -1;
    if (!force && lastAddr == address && lastDemo == demo) return;
    lastAddr = address;
    lastDemo = demo;

    tft.fillRect(0, 192, 320, 48, C_BG);
    label(footNote.c_str(), 8, 196, &F::Font2, C_DIM, Datum::top_left);
    label(address.c_str(), 8, 226, &F::FreeSansBold9pt7b, C_TEXT, Datum::middle_left);

    String b = "DEMO";
    if (demo) { b += " "; b += demo; b += "/"; b += N_SCENES; }
    tft.fillRoundRect(232, 198, 80, 36, 6, demo ? C_WARN : C_PANEL);
    label(b.c_str(), 272, 216, &F::Font2, demo ? C_INK : C_TEXT, Datum::middle_center);
}

String ageLine(const Sensor &s) {
    if (demo) return String("demo data, not a real reading");
    if (!s.everSeen) return String("nothing received yet");
    String a = "last reading ";
    a += (unsigned long)((millis() - s.lastSeen) / 1000);
    a += "s ago";
    return a;
}

void drawDetail(Sensor &s, bool force) {
    static uint32_t lastAge = 0;
    int lv = levelOf(s);
    long key = (lv + 1) * 100000L + (lv == LV_NONE ? 0 : lroundf(valueOf(s))) + (demo ? 50000L : 0);
    if (force || key != s.drawnKey) {
        s.drawnKey = key;
        force = true;
        tft.fillScreen(C_BG);
        tft.fillRect(0, 0, 320, 44, colorFor(lv));
        tft.fillRoundRect(6, 6, 84, 32, 6, C_BG);
        label("< BACK", 48, 22, &F::FreeSansBold9pt7b, C_TEXT, Datum::middle_center);
        label(s.title, 204, 22, &F::FreeSansBold12pt7b, textOn(lv), Datum::middle_center);

        label(shownValue(s).c_str(), 14, 84, &F::FreeSansBold24pt7b, C_TEXT, Datum::middle_left);
        label(wordFor(lv), 306, 74, &F::FreeSansBold12pt7b, colorFor(lv), Datum::middle_right);
        label(s.scale, 306, 98, &F::Font2, C_DIM, Datum::middle_right);

        label(s.tips[lv + 1][0], 14, 124, &F::FreeSans9pt7b, C_TEXT, Datum::top_left);
        label(s.tips[lv + 1][1], 14, 148, &F::FreeSans9pt7b, C_TEXT, Datum::top_left);

        tft.drawFastHLine(14, 182, 292, C_PANEL);
        String t = "warning at ";
        t += (int)s.warnAt; t += s.suffix;
        t += "    danger at ";
        t += (int)s.dangerAt; t += s.suffix;
        label(t.c_str(), 14, 190, &F::Font2, C_DIM, Datum::top_left);
    }
    // the "x seconds ago" line ticks on its own
    if (force || millis() - lastAge >= 1000) {
        lastAge = millis();
        tft.fillRect(0, 212, 320, 28, C_BG);
        label(ageLine(s).c_str(), 14, 214, &F::Font2, C_DIM, Datum::top_left);
    }
}

void draw() {
    bool force = redrawAll;
    redrawAll = false;
    if (detail) {
        drawDetail(*detail, force);
        return;
    }
    if (force) tft.fillScreen(C_BG);
    drawBanner(force);
    drawCard(flood, 8, force);
    drawCard(air, 164, force);
    drawFooter(force);
}

// ---------- touch ----------

// a tap only counts after two reads in a row, so pressure from the case doesn't fire it
bool readTap(int &tx, int &ty) {
    static uint8_t held = 0;
    static uint32_t lastTap = 0;
    int32_t x = 0, y = 0;
    if (!tft.getTouch(&x, &y)) { held = 0; return false; }
    if (held < 255) held++;
    if (held != 2 || millis() - lastTap < 300) return false;
    lastTap = millis();
    tx = 319 - x; ty = y;   // my touch panel reads left and right backwards
    return true;
}

void onTap(int x, int y) {
    if (detail) {
        detail = nullptr;   // anywhere on the detail screen goes back, easier to hit
    } else if (y >= 190 && x >= 220) {
        demo = (demo + 1) % (N_SCENES + 1);
        return;   // footer notices the change itself
    } else if (y >= 50 && y < 190) {
        detail = (x < 160) ? &flood : &air;
    } else {
        return;
    }
    redrawAll = true;
}

// ---------- main ----------

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(SCREEN_ROTATION);
    tft.setBrightness(255);
    tft.fillScreen(C_BG);
    label("Flood + air station", 160, 100, &F::FreeSansBold12pt7b, C_TEXT, Datum::middle_center);
    label("starting wifi " AP_SSID "...", 160, 136, &F::Font2, C_DIM, Datum::middle_center);
    startNetwork();
}

void loop() {
    server.handleClient();

    static uint32_t lastTouch = 0, lastDraw = 0;
    uint32_t now = millis();

    if (now - lastTouch >= 20) {
        lastTouch = now;
        int x, y;
        if (readTap(x, y)) {
            Serial.printf("tap %d,%d\n", x, y);   // so i can see where the screen thinks i tapped
            onTap(x, y);
        }
    }

    if (now - lastDraw >= 100) {
        lastDraw = now;
        trackLevels();
        draw();
    }
}