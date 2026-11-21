#pragma once
// panel + touch setup for the ESP32-2432S028R, copied from nowtron since it's proven on my board
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

class LGFX : public lgfx::LGFX_Device {
#ifdef CYD_ST7789
    lgfx::Panel_ST7789 _panel;
#else
    lgfx::Panel_ILI9341 _panel;
#endif
    lgfx::Bus_SPI _bus;
    lgfx::Light_PWM _light;
    lgfx::Touch_XPT2046 _touch;
public:
    LGFX() {
        { auto c = _bus.config();
          c.spi_host = HSPI_HOST; c.spi_mode = 0; c.freq_write = 40000000; c.freq_read = 16000000;
          c.pin_sclk = 14; c.pin_mosi = 13; c.pin_miso = 12; c.pin_dc = 2;
          _bus.config(c); _panel.setBus(&_bus); }
        { auto c = _panel.config();
          c.pin_cs = 15; c.pin_rst = -1; c.pin_busy = -1;
          c.panel_width = 240; c.panel_height = 320; c.offset_rotation = 0;
#ifdef CYD_ST7789
          c.invert = true;
#else
          c.invert = false;
#endif
          c.rgb_order = false; c.readable = true;
          _panel.config(c); }
        { auto c = _light.config(); c.pin_bl = 21; c.invert = false; c.freq = 44100; c.pwm_channel = 7;
          _light.config(c); _panel.setLight(&_light); }
        { auto c = _touch.config();
          // if taps land a bit off, these four numbers are the ones to nudge
          c.x_min = 300; c.x_max = 3900; c.y_min = 200; c.y_max = 3700; c.pin_int = 36; c.bus_shared = false; c.offset_rotation = 0;
          c.spi_host = VSPI_HOST; c.freq = 1000000; c.pin_sclk = 25; c.pin_mosi = 32; c.pin_miso = 39; c.pin_cs = 33;
          _touch.config(c); _panel.setTouch(&_touch); }
        setPanel(&_panel);
    }
};
