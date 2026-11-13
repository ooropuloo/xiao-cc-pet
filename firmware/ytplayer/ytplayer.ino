// XIAO ESP32C3 + Seeed Round Display: five pages, swipe left = next, right = previous.
//  page 0 (player): dumb JPEG terminal for the PC-side player, over USB serial or
//  Wi-Fi (TCP 5800, advertised as xiao-player.local); both carry the same protocol
//    PC -> ESP: "JP" + page tag ('G' player, 'C' Claude, 'Q' Claude and jump to it, 'T' temperature) + uint32 LE length + JPEG
//               "JPK" + uint32 1 + one byte: number of dial sectors of the question on screen
//    ESP -> PC: "P n" current page, "D x y" touch down, "U x y" tap release, "S" swipe (cancel),
//               "M x y" finger position while dialing the ring of a question, or dragging
//                       the player's progress ring to seek (~25/s),
//               "H x y" a finger held still on the player's progress ring: the seek drag starts
//                       (a swipe that starts on the rim changes page instead),
//               "HELLO" on connect / until the first frame,
//               "TEMP chip=NN.NC us=N" chip temperature and how long reading it took, every 5 s
//  page 3 (Claude Code): PC-rendered like the player, driven by the ccpet server
//  Firmware update over Wi-Fi: POST the .bin to http://xiao-player.local/update (OTA_USER/OTA_PASS)
//  page 1 (door): drawn locally, hold the button to POST the Cubelv door share pcLink over Wi-Fi
//  page 2 (system): Wi-Fi, heap, flash, chip temperature, uptime; tap the Wi-Fi lines to pick a network
//  page 4 (temperature): PC-rendered like the player, from the TEMP lines
//  page 5 (Wi-Fi picker, wifi_ui.h): not in the swipe cycle, swipe goes back to page 2
//  page 6 (night mode, sleep_ui.h): tap the bottom of page 2; screen off in the night window,
//               ESP -> PC "N <in window> <lit>" so the PC pauses frames and sends questions to TG
#include <Arduino_GFX_Library.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <HTTPUpdateServer.h>
#include <WebServer.h>
#include <TJpg_Decoder.h>
#include <WiFi.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <esp_ota_ops.h>
#include <sys/time.h>

#include "peer_link.h"
#include "ui_assets.h"
#include "secrets.h"  // WIFI_SSID, WIFI_PASS, DOOR_URL, OTA_USER, OTA_PASS

#define TFT_CS D1
#define TFT_DC D3
#define TFT_BL D6
#define TOUCH_INT D7
#define TOUCH_ADDR 0x2E
#define MAX_JPG 40000

#define SWIPE_PX 50
#define HOLD_MS 1000

#define C_BG 0x0000
#define C_GREEN 0x7EEB
#define C_RED 0xF186

Arduino_DataBus *bus = new Arduino_HWSPI(TFT_DC, TFT_CS, D8, D10, D9);
Arduino_GFX *gfx = new Arduino_GC9A01(bus, GFX_NOT_DEFINED, 0, true);

uint8_t *rxBuf, *frame;
uint32_t rxLen = 0, rxGot = 0, frameLen = 0;
int rxPage = 0, framePage = -1;  // which PC-rendered page the received / cached frame is for
bool rxJump = false;              // 'Q' frames pull the screen to the Claude page (a question is waiting)
bool rxDial = false;              // 'K' message: payload is the dial sector count, not a JPEG
bool rxGoto = false;              // 'Y' message: payload is the PC-drawn page to switch to
int dialK = 0;                    // sectors on the question dial (0 = unknown)
int dialShown = -1;               // sector the local pointer arc is drawn on
unsigned long lastQMs = 0;        // last 'Q' frame; a question is on screen while these keep coming
bool dialDrag = false;            // this touch started on the ring of a question: dial, don't swipe
bool seekDrag = false;            // held on the player's progress ring: seek, don't swipe
bool seekPending = false;         // on the ring but not held long enough yet: may still be a swipe
unsigned long seekEndMs = 0;      // last seek touch lifted; the chip blinks off mid-drag, so a quick re-touch goes on
#define SEEK_HOLD_MS 400
#define SEEK_STILL_PX 12
unsigned long lastMoveEmit = 0;
int magicPos = 0;
enum { WAIT_MAGIC, READ_LEN, READ_DATA } rxState = WAIT_MAGIC;
uint8_t lenBuf[4];
int lenPos = 0;
unsigned long lastByte = 0;
unsigned long lastHello = 0;

#define PAGES 6           // the swipe cycle; PAGE_WIFI and PAGE_NIGHT sit past its end
#define PAGE_CLAUDE 3
#define PAGE_TEMP 4
#define PAGE_CHAT 5
int page = 0;


bool touching = false;
int downX, downY, lastX, lastY;
unsigned long downMs = 0, lastTouchMs = 0;
bool gestureDone = false;  // swipe or door action already fired for this touch

enum DoorState { DOOR_IDLE, DOOR_HOLD, DOOR_OPENING, DOOR_OK, DOOR_FAIL };
DoorState doorState = DOOR_IDLE;
unsigned long doorStateMs = 0;
int doorHttp = 0;
int holdDrawn = -1;
int wifiDrawn = -1;

#define LINK_PORT 5800
WiFiServer linkServer(LINK_PORT);
WebServer otaWeb(80);
HTTPUpdateServer otaUpdater;
WiFiClient pcLink;
bool netStarted = false;
bool webStarted = false;

// protocol/log lines go to whichever links are up
void emit(const char *fmt, ...) {
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  Serial.print(buf);
  if (pcLink && pcLink.connected()) pcLink.print(buf);
}

bool onBlock(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t *bmp) {
  gfx->draw16bitRGBBitmap(x, y, bmp, w, h);
  return true;
}

bool readTouch(int &x, int &y) {
  if (digitalRead(TOUCH_INT) != LOW) return false;
  uint8_t t[5] = {0};
  if (Wire.requestFrom(TOUCH_ADDR, 5) != 5) return false;
  Wire.readBytes(t, 5);
  if (t[0] != 0x01) return false;
  if (t[2] == 255 || t[4] == 255) return false;  // controller glitch, not a real point
  x = t[2];
  y = t[4];
  return true;
}

// ---------- player page ----------

bool isRemotePage(int p) { return p == 0 || p == PAGE_CLAUDE || p == PAGE_TEMP || p == PAGE_CHAT; }

bool questionOnScreen() { return page == PAGE_CLAUDE && lastQMs && millis() - lastQMs < 1500; }

// Instant dial feedback: a yellow arc on the rim over the sector under the finger, drawn here
// so it doesn't wait for the PC's next frame (decoding one takes ~150 ms).
// Geometry matches clawd.render_question: sector 0 centred on 12 o'clock, clockwise.
#define DIAL_PTR 0xFEAE   // (255, 212, 112)
#define DIAL_RIM 0x2967   // (44, 47, 58), the rim colour in the PC frame
void dialArc(int sector, uint16_t color) {
  float seg = 360.0f / dialK;
  float a0 = -90 - seg / 2 + sector * seg + 3;
  float a1 = a0 + seg - 6;
  if (a0 < 0) { a0 += 360; a1 += 360; }
  if (a1 > 360) {
    gfx->fillArc(120, 120, 119, 114, a0, 359.9, color);
    gfx->fillArc(120, 120, 119, 114, 0, a1 - 360, color);
  } else {
    gfx->fillArc(120, 120, 119, 114, a0, a1, color);
  }
}

void dialPointer(int x, int y) {
  if (dialK <= 0) return;
  float seg = 360.0f / dialK;
  float a = atan2f(y - 120, x - 120) * 180 / PI + 90 + seg / 2;
  while (a < 0) a += 360;
  int sector = ((int)(a / seg)) % dialK;
  if (sector == dialShown) return;
  if (dialShown >= 0) dialArc(dialShown, DIAL_RIM);
  dialArc(sector, DIAL_PTR);
  dialShown = sector;
}

void drawRemote() {
  if (frameLen && framePage == page) {
    TJpgDec.drawJpg(0, 0, frame, frameLen);
  } else {
    gfx->fillScreen(C_BG);
    gfx->setFont((const GFXfont *)nullptr);
    gfx->setTextSize(2);
    gfx->setTextColor(RGB565_WHITE);
    gfx->setCursor(42, 110);
    gfx->print("Waiting PC...");
  }
}

bool frameDone = false;  // set by feedByte when a whole frame was received (and drawn)

void feedByte(uint8_t b) {
    lastByte = millis();
    switch (rxState) {
      case WAIT_MAGIC:
        if (magicPos == 2 && (b == 'G' || b == 'C' || b == 'Q' || b == 'K' || b == 'T' || b == 'M' || b == 'Y')) {
          rxPage = b == 'G' ? 0 : b == 'T' ? PAGE_TEMP : b == 'M' ? PAGE_CHAT : PAGE_CLAUDE;
          rxJump = b == 'Q';
          rxDial = b == 'K';
          rxGoto = b == 'Y';
          rxState = READ_LEN; lenPos = 0; magicPos = 0;
        } else if (magicPos < 2 && b == "JP"[magicPos]) {
          magicPos++;
        } else {
          magicPos = (b == 'J') ? 1 : 0;
        }
        break;
      case READ_LEN:
        lenBuf[lenPos++] = b;
        if (lenPos == 4) {
          rxLen = lenBuf[0] | (lenBuf[1] << 8) | (lenBuf[2] << 16) | ((uint32_t)lenBuf[3] << 24);
          rxGot = 0;
          rxState = (rxLen > 0 && rxLen <= MAX_JPG) ? READ_DATA : WAIT_MAGIC;
        }
        break;
      case READ_DATA:
        rxBuf[rxGot++] = b;
        if (rxGot == rxLen && rxDial) {
          dialK = rxBuf[0];
          rxState = WAIT_MAGIC;
        } else if (rxGot == rxLen && rxGoto) {
          // never pulled away from a finger, a question, or a page the board draws itself (door, settings)
          if (!touching && isRemotePage(page) && !questionOnScreen() && isRemotePage(rxBuf[0])) showPage(rxBuf[0]);
          rxState = WAIT_MAGIC;
        } else if (rxGot == rxLen) {
          memcpy(frame, rxBuf, rxLen);
          frameLen = rxLen;
          framePage = rxPage;
          // every question frame is 'Q'; only the first of a question pulls the screen over,
          // so the user can still swipe away from a question they want to ignore
          bool newQuestion = rxJump && millis() - lastQMs > 2000;
          if (rxJump) lastQMs = millis();
          if (newQuestion && page != framePage) showPage(framePage);
          else if (page == framePage) TJpgDec.drawJpg(0, 0, frame, frameLen);
          if (page == framePage) dialShown = -1;  // a fresh frame painted over the pointer
          frameDone = true;
          rxState = WAIT_MAGIC;
          emit("OK\n");
        }
        break;
    }
}

void setupWifiWeb();  // wifi_ui.h
void emitNight();     // sleep_ui.h
void guardNote(char *out, size_t size);  // wifi_guard.h
void drawNightLine();

// the web server also serves the Wi-Fi setup page on the board's own hotspot, before any network is up
void ensureWeb() {
  if (webStarted) return;
  webStarted = true;
  otaUpdater.setup(&otaWeb, "/update", OTA_USER, OTA_PASS);
  setupWifiWeb();
  otaWeb.begin();
}

void startNetServices() {
  if (netStarted || WiFi.status() != WL_CONNECTED) return;
  netStarted = true;
  linkServer.begin();
  linkServer.setNoDelay(true);
  ensureWeb();
  if (MDNS.begin("xiao-player")) {
    MDNS.addService("xiaoplayer", "tcp", LINK_PORT);
    MDNS.addService("http", "tcp", 80);
  }
}

void handleLinks() {
  while (Serial.available()) feedByte(Serial.read());

  startNetServices();
  if (netStarted && linkServer.hasClient()) {
    if (pcLink) pcLink.stop();  // newest PC connection wins (e.g. player restarted)
    pcLink = linkServer.accept();
    pcLink.setNoDelay(true);
    rxState = WAIT_MAGIC;
    pcLink.print("HELLO\n");
    pcLink.printf("P %d\n", page);
    emitNight();
    char guard[112];
    guardNote(guard, sizeof guard);
    pcLink.printf("WIFI %s\n", guard);
  }
  if (pcLink && pcLink.connected()) {
    // At most one frame per loop pass: with frames streaming in back to back, draining
    // the socket here starved the touch polling below it (no touch events at all at 10 fps).
    uint8_t chunk[512];
    int n;
    frameDone = false;
    while (!frameDone && (n = pcLink.available()) > 0) {
      n = pcLink.read(chunk, n > (int)sizeof chunk ? sizeof chunk : n);
      for (int i = 0; i < n; i++) {
        feedByte(chunk[i]);
        if (frameDone) {
          for (int j = i + 1; j < n; j++) feedByte(chunk[j]);  // keep the rest of this chunk
          break;
        }
      }
    }
  }
  if (rxState != WAIT_MAGIC && millis() - lastByte > 500) rxState = WAIT_MAGIC;
  if (webStarted) otaWeb.handleClient();
}

// ---------- door page ----------
// Backgrounds come from door_assets.h (rendered by host/gen_door_assets.py);
// only the hold ring and the Wi-Fi dot are painted live.

#define DOOR_CX 120
#define DOOR_CY 112
#define RING_IN 65
#define RING_OUT 70
#define C_RING 0xFDAD

void drawDoorImage() {
  const uint8_t *img = IMG_DOOR_IDLE;
  uint32_t len = IMG_DOOR_IDLE_LEN;
  switch (doorState) {
    case DOOR_OPENING: img = IMG_DOOR_OPENING; len = IMG_DOOR_OPENING_LEN; break;
    case DOOR_OK: img = IMG_DOOR_OK; len = IMG_DOOR_OK_LEN; break;
    case DOOR_FAIL:
      if (doorHttp == 409) { img = IMG_DOOR_FAIL_BUSY; len = IMG_DOOR_FAIL_BUSY_LEN; }
      else if (doorHttp <= 0) { img = IMG_DOOR_FAIL_NET; len = IMG_DOOR_FAIL_NET_LEN; }
      else { img = IMG_DOOR_FAIL; len = IMG_DOOR_FAIL_LEN; }
      break;
    default: break;
  }
  TJpgDec.drawJpg(0, 0, img, len);
  holdDrawn = -1;
  wifiDrawn = -1;
  drawWifi();
}

void drawWifi() {
  int st = WiFi.status() == WL_CONNECTED ? 1 : 0;
  if (st == wifiDrawn) return;
  wifiDrawn = st;
  gfx->fillCircle(156, 30, 3, st ? C_GREEN : C_RED);
}

// arc from 12 o'clock clockwise; fillArc measures degrees from 3 o'clock clockwise
void arcSeg(float a0, float a1) {
  a0 += 270; a1 += 270;
  if (a0 < 360 && a1 > 360) {
    gfx->fillArc(DOOR_CX, DOOR_CY, RING_OUT, RING_IN, a0, 359.9, C_RING);
    a0 = 360;
  }
  gfx->fillArc(DOOR_CX, DOOR_CY, RING_OUT, RING_IN, fmodf(a0, 360), a1 >= 360 ? a1 - 360 : a1, C_RING);
}

void drawHoldProgress(float frac) {
  int n = (int)(frac * 120);  // 3-degree steps
  int from = holdDrawn < 0 ? 0 : holdDrawn;
  if (n > from) arcSeg(from * 3.0f, n * 3.0f);
  holdDrawn = n;
}

void drawDoor() { drawDoorImage(); }

void setDoorState(DoorState st) {
  doorState = st;
  doorStateMs = millis();
  if (page == 1 && st != DOOR_HOLD) drawDoorImage();
}

// wall-clock ms (NTP) so device timings can be lined up with the server log
long long wallMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

void openDoor() {
  unsigned long t0 = millis();
  setDoorState(DOOR_OPENING);
  unsigned long t1 = millis();
  long long w1 = wallMs();
  doorHttp = -1;
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.setTimeout(8000);
    if (http.begin(DOOR_URL)) {
      http.addHeader("Content-Type", "application/json");
      doorHttp = http.POST("{}");
      http.end();
    }
  }
  unsigned long t2 = millis();
  emit("DOOR %d draw=%lums post=%lums wall_start=%lld wall_end=%lld\n", doorHttp, t1 - t0, t2 - t1, w1, wallMs());
  setDoorState(doorHttp == 200 ? DOOR_OK : DOOR_FAIL);
}

bool inDoorButton(int x, int y) {
  int dx = x - DOOR_CX, dy = y - DOOR_CY;
  return dx * dx + dy * dy <= (RING_OUT + 8) * (RING_OUT + 8);
}

void cancelHold() {
  doorState = DOOR_IDLE;
  if (holdDrawn > 0) drawDoorImage();
  holdDrawn = -1;
}

void doorTick() {
  drawWifi();
  if ((doorState == DOOR_OK || doorState == DOOR_FAIL) && millis() - doorStateMs > 4000) setDoorState(DOOR_IDLE);
}

// ---------- system page ----------
// Labels and bar tracks are in IMG_SYS_BG; rows must match SYS_ROWS in gen_ui_assets.py.

#define SYS_BG 0x0862
#define SYS_TRACK 0x2126
#define SYS_X0 54
#define SYS_X1 204
#define ROW_WIFI 66
#define ROW_RAM 100
#define ROW_FLASH 134
#define ROW_CPU 168
#define ROW_UP 196

unsigned long sysDrawnMs = 0;

void sysValue(int rowY, const char *text, int x0 = SYS_X0) {
  gfx->fillRect(x0, rowY - 9, SYS_X1 + 4 - x0, 16, SYS_BG);
  gfx->setFont(u8g2_font_helvB10_tr);
  gfx->setTextSize(1);
  gfx->setTextColor(RGB565_WHITE);
  int16_t bx, by;
  uint16_t bw, bh;
  gfx->getTextBounds(text, 0, 0, &bx, &by, &bw, &bh);
  gfx->setCursor(SYS_X1 - bw, rowY + 5);
  gfx->print(text);
}

void sysBar(int rowY, float frac, uint16_t color) {
  if (frac < 0) frac = 0;
  if (frac > 1) frac = 1;
  int y = rowY + 10;
  gfx->fillRoundRect(SYS_X0, y, SYS_X1 - SYS_X0, 5, 2, SYS_TRACK);
  int w = (int)((SYS_X1 - SYS_X0) * frac);
  if (w > 3) gfx->fillRoundRect(SYS_X0, y, w, 5, 2, color);
}

void drawSysValues() {
  char buf[40];
  bool up = WiFi.status() == WL_CONNECTED;

  gfx->fillRect(30, 34, 180, 14, SYS_BG);
  gfx->setFont(u8g2_font_helvR08_tr);
  gfx->setTextSize(1);
  gfx->setTextColor(0x9CF3);
  if (up) snprintf(buf, sizeof buf, "%s  %s", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
  else snprintf(buf, sizeof buf, "Wi-Fi connecting...");
  int16_t bx, by;
  uint16_t bw, bh;
  gfx->getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
  gfx->setCursor(120 - bw / 2, 45);
  gfx->print(buf);

  int rssi = up ? WiFi.RSSI() : -100;
  if (up) snprintf(buf, sizeof buf, "%d dBm", rssi); else snprintf(buf, sizeof buf, "offline");
  sysValue(ROW_WIFI, buf);
  sysBar(ROW_WIFI, (rssi + 90) / 60.0f, rssi > -67 ? C_GREEN : rssi > -80 ? 0xFE60 : C_RED);

  uint32_t heap = ESP.getHeapSize(), freeHeap = ESP.getFreeHeap();
  snprintf(buf, sizeof buf, "%lu / %lu KB", (unsigned long)(heap - freeHeap) / 1024, (unsigned long)heap / 1024);
  sysValue(ROW_RAM, buf);
  sysBar(ROW_RAM, (heap - freeHeap) / (float)heap, 0xFD20);

  const esp_partition_t *part = esp_ota_get_running_partition();
  uint32_t sketch = ESP.getSketchSize(), partSize = part ? part->size : sketch;
  snprintf(buf, sizeof buf, "%.2f / %.2f MB", sketch / 1048576.0f, partSize / 1048576.0f);
  sysValue(ROW_FLASH, buf);
  sysBar(ROW_FLASH, sketch / (float)partSize, 0x4D7F);

  float temp = temperatureRead();
  snprintf(buf, sizeof buf, "%.0f C", temp);
  sysValue(ROW_CPU, buf);
  sysBar(ROW_CPU, (temp - 20) / 60.0f, temp < 60 ? 0x07FF : C_RED);

  unsigned long sec = millis() / 1000;
  snprintf(buf, sizeof buf, "%lud %02lu:%02lu:%02lu", sec / 86400, (sec / 3600) % 24, (sec / 60) % 60, sec % 60);
  sysValue(ROW_UP, buf, 62);
}

void drawSys() {
  TJpgDec.drawJpg(0, 0, IMG_SYS_BG, IMG_SYS_BG_LEN);
  drawSysValues();
  drawNightLine();
  sysDrawnMs = millis();
}

void sysTick() {
  if (page == 2 && millis() - sysDrawnMs >= 1000) {
    drawSysValues();
    sysDrawnMs = millis();
  }
}

// Reported only while someone can hear it. The sensor stays on after the first read; the
// duration goes out with the value so a slow read would show up in player.log.
#define TEMP_EVERY_MS 5000
unsigned long tempLastMs = 0;

void tempTick() {
  if (millis() - tempLastMs < TEMP_EVERY_MS) return;
  tempLastMs = millis();
  if (!(pcLink && pcLink.connected()) && !Serial) return;
  unsigned long t0 = micros();
  float chip = temperatureRead();
  unsigned long us = micros() - t0;
  emit("TEMP chip=%.1fC us=%lu\n", chip, us);
}

#include "wifi_ui.h"
#include "sleep_ui.h"
#include "wifi_guard.h"

// ---------- pages / touch ----------

// timing probe for the network leg, no door involved. Runs in its own task: a slow door
// box used to freeze the whole screen for up to 2 x 5 s on every visit to the door page.
volatile bool pingBusy = false, pingDone = false;
int pingCode[2];
unsigned long pingMs[2];

void pingTask(void *) {
  String url = DOOR_URL;
  url.replace("/api/open", "/api/health");
  for (int i = 0; i < 2; i++) {
    unsigned long t = millis();
    HTTPClient http;
    http.setTimeout(5000);
    int code = -1;
    if (http.begin(url)) { code = http.GET(); http.end(); }
    pingCode[i] = code;
    pingMs[i] = millis() - t;
  }
  pingDone = true;  // reported from loop(): emit() is not safe off the main task
  pingBusy = false;
  vTaskDelete(nullptr);
}

void pingHealth() {
  if (pingBusy || WiFi.status() != WL_CONNECTED) return;
  pingBusy = true;
  if (xTaskCreate(pingTask, "ping", 6144, nullptr, 1, nullptr) != pdPASS) pingBusy = false;
}

void pingReport() {
  if (!pingDone) return;
  pingDone = false;
  for (int i = 0; i < 2; i++) emit("DOOR ping%d %d %lums\n", i, pingCode[i], pingMs[i]);
}

void showPage(int p) {
  page = p;
  holdDrawn = -1;
  if (doorState == DOOR_HOLD) doorState = DOOR_IDLE;
  emit("P %d\n", page);
  if (isRemotePage(page)) drawRemote();
  else if (page == 1) { drawDoor(); pingHealth(); }
  else if (page == PAGE_WIFI) drawWifiUi();
  else if (page == PAGE_NIGHT) drawNightUi();
  else drawSys();
}

void onTouchDown(int x, int y) {
  downX = lastX = x; downY = lastY = y;
  downMs = millis();
  gestureDone = false;
  int cdx = x - 120, cdy = y - 120;
  dialDrag = questionOnScreen() && cdx * cdx + cdy * cdy > 72 * 72;
  bool onRim = page == 0 && cdx * cdx + cdy * cdy > 96 * 96;  // same radius as SEEK_R in player.py
  seekDrag = onRim && millis() - seekEndMs < 500;
  seekPending = onRim && !seekDrag;
  if (dialDrag) dialPointer(x, y);
  if (page == PAGE_WIFI) wifiTouchDown(x, y);
  if (page == PAGE_NIGHT) nightTouchDown(x, y);
  if (isRemotePage(page)) emit("D %d %d\n", x, y);
  if (seekDrag) emit("H %d %d\n", x, y);
  if (page == 1 && inDoorButton(x, y) && (doorState == DOOR_IDLE || doorState == DOOR_OK || doorState == DOOR_FAIL)) {
    if (doorState != DOOR_IDLE) { doorState = DOOR_IDLE; drawDoorImage(); }
    doorState = DOOR_HOLD;
    holdDrawn = 0;
  }
}

void onTouchMove(int x, int y) {
  if (seekPending) {
    if (abs(x - downX) > SEEK_STILL_PX || abs(y - downY) > SEEK_STILL_PX) {
      seekPending = false;  // moved before the hold: an ordinary swipe / tap
    } else if (millis() - downMs >= SEEK_HOLD_MS) {
      seekPending = false;
      seekDrag = true;
      emit("H %d %d\n", x, y);  // the PC shows the knob; from here the finger drags it
      lastX = x; lastY = y;
      return;
    }
  }
  if (seekDrag) {  // the PC draws the scrub position; just stream the finger
    if ((x != lastX || y != lastY) && millis() - lastMoveEmit >= 40) {
      emit("M %d %d\n", x, y);
      lastMoveEmit = millis();
    }
    lastX = x; lastY = y;
    return;
  }
  if (dialDrag) {
    dialPointer(x, y);
    if ((x != lastX || y != lastY) && millis() - lastMoveEmit >= 40) {
      emit("M %d %d\n", x, y);
      lastMoveEmit = millis();
    }
    lastX = x; lastY = y;
    return;
  }
  lastX = x; lastY = y;
  if (page == PAGE_WIFI && wifiDrag) { wifiTouchMove(x, y); return; }  // ring scrolls, never swipes
  if (page == PAGE_NIGHT && nightDrag) { nightTouchMove(x, y); return; }  // ring sets the hour
  int dx = x - downX, dy = y - downY;
  if (gestureDone) return;
  if (abs(dx) > SWIPE_PX && abs(dx) > abs(dy)) {
    gestureDone = true;
    if (isRemotePage(page)) emit("S\n");
    if (doorState == DOOR_HOLD) doorState = DOOR_IDLE;
    if (page == PAGE_WIFI) { wifiCenterDown = false; wifiLeave(); showPage(2); return; }
    if (page == PAGE_NIGHT) { showPage(2); return; }
    showPage((page + (dx < 0 ? 1 : PAGES - 1)) % PAGES);
    return;
  }
  if (page == 1 && doorState == DOOR_HOLD) {
    if (abs(dx) > 25 || abs(dy) > 25) {  // moved off: cancel the hold
      cancelHold();
      return;
    }
    float frac = (millis() - downMs) / (float)HOLD_MS;
    drawHoldProgress(frac > 1 ? 1 : frac);
    if (frac >= 1) {
      gestureDone = true;
      openDoor();
    }
  }
}

void onTouchUp() {
  if (seekDrag) seekEndMs = millis();
  seekPending = false;
  if (isRemotePage(page) && !gestureDone) emit("U %d %d\n", lastX, lastY);
  if (page == 1 && doorState == DOOR_HOLD) cancelHold();
  if (page == 2 && !gestureDone && lastY < 84) wifiEnter();  // the SSID / signal lines
  else if (page == 2 && !gestureDone && lastY > 184) nightEnter();  // uptime / night line
  else if (page == PAGE_NIGHT && !gestureDone) nightTap(lastX, lastY);
  else if (page == PAGE_WIFI && !gestureDone) wifiTap(lastX, lastY);
}

void setup() {
  Serial.setRxBufferSize(16384);
  Serial.begin(115200);
  // USB plugged into a PC with nobody reading COM: HWCDC would block each print
  // for up to 20 x 100 ms and stall the whole loop. Drop the bytes instead.
  Serial.setTxTimeoutMs(0);
  rxBuf = (uint8_t *)malloc(MAX_JPG);
  frame = (uint8_t *)malloc(MAX_JPG);

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  pinMode(TOUCH_INT, INPUT_PULLUP);
  Wire.begin();
  gfx->begin(40000000);

  TJpgDec.setJpgScale(1);
  TJpgDec.setSwapBytes(false);
  TJpgDec.setCallback(onBlock);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);  // modem sleep held touch events until the PC sent something
  guardBegin();
  loadSaved();
  wifiBegin();
  configTzTime("CST-8", "time.google.com", "pool.ntp.org");  // Taiwan; wallMs() stays UTC
  loadNight();

  showPage(0);
}

void loop() {
  handleLinks();

  int x, y;
  // CHSC6X pulses INT while held, so only count a release after a quiet gap
  if (readTouch(x, y)) {
    lastTouchMs = millis();
    if (!touching) {
      touching = true;
      swallowTouch = nightWake();  // a dark screen: this touch only turns it back on
      if (!swallowTouch) onTouchDown(x, y);
    } else if (!swallowTouch) {
      onTouchMove(x, y);
    }
  } else if (touching) {
    if (millis() - lastTouchMs > 120) {
      touching = false;
      if (!swallowTouch) onTouchUp();
      swallowTouch = false;
    } else if (!swallowTouch && ((page == 1 && doorState == DOOR_HOLD) || seekPending)) {
      onTouchMove(lastX, lastY);  // keep the hold timers running between INT pulses
    }
  }

  if (page == 1) doorTick();
  sysTick();
  tempTick();
  wifiTick();
  guardTick();
  nightTick();
  pingReport();
  char peerLine[64];
  if (peerTick("xiao", peerLine, sizeof peerLine)) emit("%s", peerLine);

  if (!frameLen && millis() - lastHello > 1000) {
    lastHello = millis();
    emit("HELLO\n");
  }
}
