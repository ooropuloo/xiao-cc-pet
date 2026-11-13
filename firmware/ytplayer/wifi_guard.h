// Wi-Fi watchdog (round display). 2026-11-11 the board sat off the network for 50 minutes until it was
// replugged. Offline 30 s -> disconnect and wifiBegin() again; offline longer -> save why and restart.
// The restart wait doubles with each restart that did not get back online (60 s .. 32 min), so a board
// carried away from every saved network does not reboot every minute.
// Paused while the Wi-Fi page is trying a network or the setup hotspot is on (wifi_ui.h owns those).
// The note lives in RTC memory, so it survives ESP.restart() (not a power cut); it goes to the PC as
// "WIFI guard ..." when the link opens (player.py logs WIFI lines).
// Include after wifi_ui.h: uses trying, portalOn, wifiBegin(), emit().
#pragma once
#include <WiFi.h>

#define GUARD_RETRY_MS 30000UL
#define GUARD_RESTART_MS 60000UL
#define GUARD_MAX_SHIFT 5
#define GUARD_MAGIC 0x5849414FUL  // "XIAO"

RTC_NOINIT_ATTR uint32_t guardMagic;
RTC_NOINIT_ATTR uint32_t guardRestarts;  // restarts since power-on
RTC_NOINIT_ATTR uint32_t guardStreak;    // restarts since the last time Wi-Fi was up
RTC_NOINIT_ATTR int32_t guardLastReason;
RTC_NOINIT_ATTR uint32_t guardLastUp;

volatile int wifiDropReason = 0;  // last STA_DISCONNECTED reason, 0 = none since boot
unsigned long guardOffSince = 0;
bool guardRetried = false;
uint32_t guardDrops = 0;

void guardBegin() {
  if (esp_reset_reason() == ESP_RST_POWERON || guardMagic != GUARD_MAGIC) {
    guardMagic = GUARD_MAGIC;
    guardRestarts = 0;
    guardStreak = 0;
    guardLastReason = 0;
    guardLastUp = 0;
  }
  WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t info) {
    wifiDropReason = info.wifi_sta_disconnected.reason;
    Serial.printf("wifi disconnected reason=%d\n", wifiDropReason);
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
}

// "guard reset=R restarts=N lastReason=R lastUp=Ns drops=D reason=R"
void guardNote(char *out, size_t size) {
  snprintf(out, size, "guard reset=%d restarts=%lu lastReason=%ld lastUp=%lus drops=%lu reason=%d",
           (int)esp_reset_reason(), (unsigned long)guardRestarts, (long)guardLastReason,
           (unsigned long)guardLastUp, (unsigned long)guardDrops, wifiDropReason);
}

void guardTick() {
  static bool everConnected = false;
  if (WiFi.status() == WL_CONNECTED || trying || portalOn) {
    if (WiFi.status() == WL_CONNECTED) {
      everConnected = true;
      guardStreak = 0;
    }
    guardOffSince = 0;
    guardRetried = false;
    return;
  }
  unsigned long now = millis();
  if (guardOffSince == 0) {
    guardOffSince = now ? now : 1;
    if (everConnected) guardDrops++;
    return;
  }
  unsigned long off = now - guardOffSince;
  if (!guardRetried && off > GUARD_RETRY_MS) {
    guardRetried = true;
    emit("WIFI guard offline 30s reason=%d, reconnecting\n", wifiDropReason);
    WiFi.disconnect();
    wifiBegin();
  }
  uint32_t shift = guardStreak < GUARD_MAX_SHIFT ? guardStreak : GUARD_MAX_SHIFT;
  if (off > (GUARD_RESTART_MS << shift)) {
    guardRestarts++;
    guardStreak++;
    guardLastReason = wifiDropReason;
    guardLastUp = now / 1000;
    emit("WIFI guard offline %lus reason=%d, restarting\n", off / 1000, wifiDropReason);
    delay(100);
    ESP.restart();
  }
}
