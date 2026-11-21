#pragma once
// the only file i should need to touch

// the board always makes its own network with this name
#define AP_SSID "FloodAir"
#define AP_PASS "floodair123"   // has to be 8+ characters

// hotspot i also join, only so i can reach the internet (2.4GHz, campus wifi won't work)
// FloodAir runs either way. without the hotspot the ai scores just show --
#define WIFI_SSID "YOUR_HOTSPOT_NAME"
#define WIFI_PASS "YOUR_HOTSPOT_PASSWORD"

// my teammate's model on railway. paste the railway link and keep /predict on the end
#define PREDICT_URL "https://YOUR-APP.up.railway.app/predict"
#define PREDICT_EVERY_MS 15000
#define AI_LABEL "AI score"   // what the number is called on the screen

// also reachable at http://floodair.local when the network allows it
#define HOSTNAME "floodair"

// 1 = usb on the right, 3 = flipped. swap if the picture is upside down in the case
#define SCREEN_ROTATION 1

// water level, as a percent of the sensor's range
#define WATER_WARN   40
#define WATER_DANGER 70
#define WATER_MAX    100

// air quality index, the 0-500 scale (over 100 is bad for sensitive people, over 150 for everyone)
#define AQI_WARN   101
#define AQI_DANGER 151
#define AQI_MAX    300   // just where the bar on the website tops out

// if a sensor goes quiet this long i stop trusting its last number
#define STALE_AFTER_MS 30000