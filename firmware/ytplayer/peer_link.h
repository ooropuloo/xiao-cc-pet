// ESP-NOW presence beacon, the same file in both firmwares: each board broadcasts a short tag and
// reports how strongly it hears the other one. Only signal strength is used, so the tag carries no data.
// While connected to Wi-Fi the radio stays on the access point's channel: boards on different
// channels can't hear each other, which is why the report includes the channel.
#pragma once
#include <WiFi.h>
#include <esp_now.h>

#define PEER_BEACON_MS 250
#define PEER_REPORT_MS 1000
#define PEER_TAG "MFPEER1"

static volatile int peerRssi = 0;
static volatile uint32_t peerHeardMs = 0, peerHeardCount = 0;
static bool peerReady = false;
static uint32_t peerSentMs = 0, peerReportMs = 0;
static const uint8_t PEER_BROADCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

static void onPeerRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len != sizeof PEER_TAG || memcmp(data, PEER_TAG, len) != 0) return;
  peerRssi = info->rx_ctrl->rssi;
  peerHeardMs = millis();
  peerHeardCount = peerHeardCount + 1;
}

static bool peerBegin() {
  if (esp_now_init() != ESP_OK) return false;
  esp_now_register_recv_cb(onPeerRecv);
  esp_now_peer_info_t p = {};
  memcpy(p.peer_addr, PEER_BROADCAST, 6);
  p.channel = 0;  // whatever channel the radio is on
  p.ifidx = WIFI_IF_STA;
  p.encrypt = false;
  return esp_now_add_peer(&p) == ESP_OK;
}

// Call from loop(). Fills `line` and returns true once per PEER_REPORT_MS:
// "PEER <name> <rssi|none> ch=<channel> n=<beacons heard> age=<ms since the last one>\n"
static bool peerTick(const char *name, char *line, size_t size) {
  if (WiFi.status() != WL_CONNECTED) return false;
  uint32_t now = millis();
  if (!peerReady) peerReady = peerBegin();
  if (!peerReady) return false;
  if (now - peerSentMs >= PEER_BEACON_MS) {
    peerSentMs = now;
    esp_now_send(PEER_BROADCAST, (const uint8_t *)PEER_TAG, sizeof PEER_TAG);
  }
  if (now - peerReportMs < PEER_REPORT_MS) return false;
  peerReportMs = now;
  uint32_t heard = peerHeardMs, count = peerHeardCount;
  if (count == 0) {
    snprintf(line, size, "PEER %s none ch=%d n=0 age=0\n", name, WiFi.channel());
  } else {
    snprintf(line, size, "PEER %s %d ch=%d n=%lu age=%lu\n", name, peerRssi, WiFi.channel(),
             (unsigned long)count, (unsigned long)(now - heard));
  }
  return true;
}
