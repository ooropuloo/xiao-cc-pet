// Wi-Fi picker (page PAGE_WIFI, opened by tapping the Wi-Fi line on the system page).
//  - list: scan, strongest first, laid out like the question dial: one ring sector per network
//    (sector 0 at 12 o'clock, clockwise); drag along the ring to pick, the centre shows the
//    picked one in big type, tap the centre to connect.
//    Ring colours: green = connected, light = saved, dark = new, yellow = picked.
//  - tap a saved network: switch to it; if it doesn't connect in 15 s, go back to the old one
//  - tap a new network: the board opens its own hotspot "XIAO-Setup" with a fresh random
//    password and shows it as a QR code; the phone joins, the captive page asks for the
//    network password, then the board tries it the same way and saves it on success
//  - while disconnected for 20 s, cycle through the saved networks (moving the board around)
//  - "channels" button at the top of the centre: 2.4 GHz channel chart, every access point as a
//    hill over the channels it covers (20/40 MHz), height = signal; rescans every 10 s, tap = back.
// Saved networks live in NVS ("wifi": n, s0..s4, p0..p4, last); the secrets.h one seeds it.
// Included from ytplayer.ino after the system page; uses its gfx / emit / otaWeb / page.
#pragma once
#include <DNSServer.h>
#include <Preferences.h>
#include <qrcode.h>

#define PAGE_WIFI 6
#define MAX_SAVED 5
#define WIFI_ROWS 12
#define WR_OUT 119
#define WR_IN 98
#define WR_CENTER 72   // touches closer than this are on the centre (same as the question dial)
#define C_PICK 0xFEAE  // the question dial's pointer yellow
#define C_GREY 0x8C71
#define PORTAL_MS (5 * 60 * 1000UL)

void showPage(int p);
void ensureWeb();

struct SavedNet { String ssid, pass; };
SavedNet saved[MAX_SAVED];
int savedN = 0, savedLast = 0;
Preferences wifiPrefs;

enum WifiUi { WUI_SCAN, WUI_LIST, WUI_CONNECTING, WUI_RESULT, WUI_PORTAL, WUI_CHART };
WifiUi wui = WUI_SCAN;
String rows[WIFI_ROWS];
int rowRssi[WIFI_ROWS];
int rowN = 0;
int wSel = 0;           // picked network
int wSelDrawn = -1;     // sector currently painted yellow
bool wifiDrag = false;  // this touch started on the ring: it scrolls, never swipes
bool wifiCenterDown = false;
unsigned long wuiMs = 0;

String tryingSsid, tryingPass, prevSsid, prevPass;
bool trySave = false, tryOk = false;
unsigned long tryMs = 0;
bool trying = false;  // a switch is in progress (also when it was started from the portal)

bool portalOn = false;
String portalPass, portalSsid;
DNSServer portalDns;
unsigned long portalMs = 0;
String pendingSsid, pendingPass;
unsigned long pendingMs = 0;  // let the HTTP reply leave before the AP goes away

unsigned long offlineSince = 0;

// channel chart: every access point of the last scan (not merged by name like the list)
#define MAX_APS 24
#define CHART_SCAN_MS 10000
struct ApRec { char ssid[33]; int8_t rssi; uint8_t ch; int8_t second; };  // second: +1 above, -1 below, 0 none
ApRec aps[MAX_APS];
int apN = 0;
bool chartScanning = false;
unsigned long chartScanMs = 0;
bool chartBtnDown = false;  // this touch started on the "channels" button

void loadSaved() {
  wifiPrefs.begin("wifi", false);
  savedN = wifiPrefs.getInt("n", 0);
  if (savedN <= 0) {  // first boot with this firmware: seed from secrets.h
    savedN = 1;
    wifiPrefs.putInt("n", 1);
    wifiPrefs.putString("s0", WIFI_SSID);
    wifiPrefs.putString("p0", WIFI_PASS);
    wifiPrefs.putInt("last", 0);
  }
  for (int i = 0; i < savedN && i < MAX_SAVED; i++) {
    saved[i].ssid = wifiPrefs.getString(("s" + String(i)).c_str(), "");
    saved[i].pass = wifiPrefs.getString(("p" + String(i)).c_str(), "");
  }
  savedLast = wifiPrefs.getInt("last", 0);
  if (savedLast >= savedN) savedLast = 0;
}

int findSaved(const String &ssid) {
  for (int i = 0; i < savedN; i++) if (saved[i].ssid == ssid) return i;
  return -1;
}

void rememberNet(const String &ssid, const String &pass) {
  int i = findSaved(ssid);
  if (i < 0) {
    if (savedN < MAX_SAVED) i = savedN++;
    else i = (savedLast + 1) % MAX_SAVED;  // full: overwrite one that isn't the current one
  }
  saved[i] = {ssid, pass};
  savedLast = i;
  wifiPrefs.putString(("s" + String(i)).c_str(), ssid);
  wifiPrefs.putString(("p" + String(i)).c_str(), pass);
  wifiPrefs.putInt("n", savedN);
  wifiPrefs.putInt("last", i);
}

void wifiBegin() {
  WiFi.begin(saved[savedLast].ssid.c_str(), saved[savedLast].pass.c_str());
}

// ---- drawing ----
void wuiText(const char *s, int y, uint16_t color, const uint8_t *font = u8g2_font_helvB10_tr) {
  gfx->setFont(font);
  gfx->setTextSize(1);
  gfx->setTextColor(color);
  int16_t bx, by;
  uint16_t bw, bh;
  gfx->getTextBounds(s, 0, 0, &bx, &by, &bw, &bh);
  gfx->setCursor(120 - bw / 2, y);
  gfx->print(s);
}

void wuiFrame(const char *title) {
  gfx->fillScreen(SYS_BG);
  wuiText(title, 34, 0x9CF3, u8g2_font_helvB10_tr);
}

uint16_t wifiRowColor(int i) {
  if (WiFi.status() == WL_CONNECTED && WiFi.SSID() == rows[i]) return C_GREEN;
  return findSaved(rows[i]) >= 0 ? 0x8C71 : 0x31A6;
}

// sector i of the ring; fillArc measures degrees clockwise from 3 o'clock
void wifiSector(int i, uint16_t color) {
  float seg = 360.0f / rowN;
  float gap = rowN > 1 ? 2.5f : 0;
  float a0 = 270 - seg / 2 + i * seg + gap, a1 = a0 + seg - 2 * gap;
  while (a0 >= 360) { a0 -= 360; a1 -= 360; }
  if (a1 > 360) {
    gfx->fillArc(120, 120, WR_OUT, WR_IN, a0, 359.9, color);
    gfx->fillArc(120, 120, WR_OUT, WR_IN, 0, a1 - 360, color);
  } else {
    gfx->fillArc(120, 120, WR_OUT, WR_IN, a0, a1, color);
  }
}

int wifiSectorAt(int x, int y) {
  float seg = 360.0f / rowN;
  float a = atan2f(y - 120, x - 120) * 180 / PI + 90 + seg / 2;
  while (a < 0) a += 360;
  return ((int)(a / seg)) % rowN;
}

// biggest font that fits the centre disc
void wifiBigName(const String &name, int y, uint16_t color) {
  const uint8_t *fonts[] = {u8g2_font_helvB18_tr, u8g2_font_helvB14_tr, u8g2_font_helvB12_tr, u8g2_font_helvB10_tr};
  char buf[33];
  snprintf(buf, sizeof buf, "%s", name.c_str());
  for (int f = 0; f < 4; f++) {
    gfx->setFont(fonts[f]);
    gfx->setTextSize(1);
    int16_t bx, by;
    uint16_t bw, bh;
    gfx->getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
    if (bw <= 160 || f == 3) {
      while (bw > 160 && strlen(buf) > 4) {  // still too long: cut and mark it
        buf[strlen(buf) - 4] = 0;
        strcat(buf, "..");
        gfx->getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
      }
      gfx->setTextColor(color);
      gfx->setCursor(120 - bw / 2, y);
      gfx->print(buf);
      return;
    }
  }
}

void drawWifiCenter() {
  gfx->fillCircle(120, 120, WR_IN - 3, SYS_BG);
  if (!rowN) {
    wuiText("no networks found", 116, C_GREY);
    wuiText("swipe = back", 140, C_GREY, u8g2_font_helvR08_tr);
    return;
  }
  char buf[40];
  // "channels" button (opens the chart); the n / N counter moved down to the dBm line
  uint16_t bc = chartBtnDown ? C_PICK : 0x9CF3;
  gfx->drawRoundRect(82, 44, 76, 18, 9, bc);
  wuiText("channels", 57, bc, u8g2_font_helvR08_tr);
  // signal bars
  int r = rowRssi[wSel], bars = r > -55 ? 4 : r > -67 ? 3 : r > -78 ? 2 : 1;
  for (int b = 0; b < 4; b++) {
    int h = 5 + b * 4;
    gfx->fillRect(108 + b * 7, 84 - h, 5, h, b < bars ? RGB565_WHITE : SYS_TRACK);
  }
  bool cur = WiFi.status() == WL_CONNECTED && WiFi.SSID() == rows[wSel];
  bool known = findSaved(rows[wSel]) >= 0;
  wifiBigName(rows[wSel], 126, cur ? C_GREEN : RGB565_WHITE);
  snprintf(buf, sizeof buf, "%d dBm   %d/%d", r, wSel + 1, rowN);
  wuiText(buf, 148, C_GREY, u8g2_font_helvR08_tr);
  const char *hint = cur ? "connected" : known ? "tap here to switch" : "tap here, then scan QR";
  uint16_t hc = cur ? C_GREEN : wifiCenterDown ? C_PICK : 0x9CF3;
  wuiText(hint, 178, hc, u8g2_font_helvB10_tr);
}

void wifiPick(int i, bool force = false) {
  if (!rowN) return;
  i = ((i % rowN) + rowN) % rowN;
  if (i == wSel && wSelDrawn == wSel && !force) return;
  if (wSelDrawn >= 0 && wSelDrawn < rowN) wifiSector(wSelDrawn, wifiRowColor(wSelDrawn));
  wSel = i;
  wifiSector(wSel, C_PICK);
  wSelDrawn = wSel;
  drawWifiCenter();
}

void drawWifiList() {
  gfx->fillScreen(SYS_BG);
  for (int i = 0; i < rowN; i++) wifiSector(i, wifiRowColor(i));
  wSelDrawn = -1;
  if (wSel >= rowN) wSel = 0;
  if (rowN) wifiPick(wSel, true);
  else drawWifiCenter();
}

void drawQr(const char *text) {
  esp_qrcode_config_t cfg = ESP_QRCODE_CONFIG_DEFAULT();
  cfg.display_func = [](esp_qrcode_handle_t q) {
    int n = esp_qrcode_get_size(q);
    int px = 120 / (n + 2);  // module size; quiet zone of one module
    int side = px * (n + 2);
    int x0 = 120 - side / 2, y0 = 112 - side / 2;
    gfx->fillRect(x0, y0, side, side, RGB565_WHITE);
    for (int y = 0; y < n; y++)
      for (int x = 0; x < n; x++)
        if (esp_qrcode_get_module(q, x, y)) gfx->fillRect(x0 + (x + 1) * px, y0 + (y + 1) * px, px, px, RGB565_BLACK);
  };
  esp_qrcode_generate(&cfg, text);
}

void drawPortal() {
  wuiFrame("Scan to set password");
  char buf[96];
  snprintf(buf, sizeof buf, "WIFI:T:WPA;S:XIAO-Setup;P:%s;;", portalPass.c_str());
  drawQr(buf);
  snprintf(buf, sizeof buf, "for: %.22s", portalSsid.c_str());
  wuiText(buf, 190, RGB565_WHITE);
  snprintf(buf, sizeof buf, "XIAO-Setup / %s  then 192.168.4.1", portalPass.c_str());
  wuiText(buf, 208, C_GREY, u8g2_font_helvR08_tr);
}

// ---- channel chart ----
// x: channel -1..15 over x 30..210; y: -95..-30 dBm over y 172..72
float chartX(float ch) { return 30 + (ch + 1) * 11.25f; }
int chartY(int rssi) {
  if (rssi < -95) rssi = -95;
  if (rssi > -30) rssi = -30;
  return 172 - (rssi + 95) * 100 / 65;
}

// centre and half-width (in channels) of what an access point occupies
void apSpan(const ApRec &a, float &c, float &w) {
  c = a.ch + 2 * a.second;  // 40 MHz: the pair's centre is two channels off the primary
  w = a.second ? 4.2f : 2.2f;
}

const uint16_t CHART_COLORS[] = {0x5D1F, 0xFB2C, 0xFEAE, 0xB57F, 0x07FF, 0xFC60, 0xF81F, 0x9FE0};

void drawChart() {
  gfx->fillScreen(SYS_BG);
  wuiText("2.4 GHz channels", 40, 0x9CF3, u8g2_font_helvB10_tr);
  // grid: dBm lines and channel ticks
  for (int r = -90; r <= -40; r += 25) {
    int y = chartY(r);
    for (int x = 34; x < 206; x += 4) gfx->drawPixel(x, y, SYS_TRACK);
    char b[8];
    snprintf(b, sizeof b, "%d", r);
    gfx->setFont(u8g2_font_helvR08_tr);
    gfx->setTextColor(0x632C);
    gfx->setCursor(14, y + 3);
    gfx->print(b);
  }
  gfx->drawFastHLine(26, 172, 188, C_GREY);
  gfx->setFont(u8g2_font_helvR08_tr);
  for (int ch = 1; ch <= 13; ch++) {
    bool main3 = ch == 1 || ch == 6 || ch == 11;
    int x = chartX(ch);
    gfx->drawFastVLine(x, 172, 3, C_GREY);
    char b[4];
    snprintf(b, sizeof b, "%d", ch);
    int16_t bx, by;
    uint16_t bw, bh;
    gfx->getTextBounds(b, 0, 0, &bx, &by, &bw, &bh);
    gfx->setTextColor(main3 ? RGB565_WHITE : 0x632C);
    gfx->setCursor(x - bw / 2, 186);
    gfx->print(b);
  }
  // hills, weakest first so the strong ones end up on top
  String cur = WiFi.status() == WL_CONNECTED ? WiFi.SSID() : "";
  int curCh = WiFi.status() == WL_CONNECTED ? WiFi.channel() : 0;
  for (int i = apN - 1; i >= 0; i--) {
    float c, w;
    apSpan(aps[i], c, w);
    bool mine = cur.length() && cur == aps[i].ssid && aps[i].ch == curCh;
    uint16_t col = mine ? C_GREEN : CHART_COLORS[i % 8];
    int base = 172, top = chartY(aps[i].rssi);
    int px = -1, py = -1;
    for (int k = 0; k <= 20; k++) {  // flat-topped hill: 1 - t^4
      float t = -1 + k / 10.0f;
      float t2 = t * t;
      int x = chartX(c + t * w), y = base - (base - top) * (1 - t2 * t2);
      if (px >= 0) {
        gfx->drawLine(px, py, x, y, col);
        gfx->drawLine(px, py - 1, x, y - 1, col);
        if (mine) gfx->drawLine(px, py - 2, x, y - 2, col);
      }
      px = x; py = y;
    }
  }
  // names of the five strongest (aps[] is strongest first), nudged up when they collide
  int lx[5], ly[5], lw[5], ln = 0;
  gfx->setFont(u8g2_font_helvR08_tr);
  for (int i = 0; i < apN && ln < 5; i++) {
    float c, w;
    apSpan(aps[i], c, w);
    char b[12];
    snprintf(b, sizeof b, "%.10s", aps[i].ssid[0] ? aps[i].ssid : "(hidden)");
    int16_t bx, by;
    uint16_t bw, bh;
    gfx->getTextBounds(b, 0, 0, &bx, &by, &bw, &bh);
    int x = chartX(c) - bw / 2, y = chartY(aps[i].rssi) - 5;
    if (x < 24) x = 24;
    if (x + bw > 216) x = 216 - bw;
    for (int k = 0; k < ln; k++)  // overlaps a placed name (with 4 px air): move above it, recheck all
      if (x < lx[k] + lw[k] + 4 && x + bw + 4 > lx[k] && abs(ly[k] - y) < 10) { y = ly[k] - 10; k = -1; }
    if (y < 56) continue;  // no room left above
    bool mine = cur.length() && cur == aps[i].ssid && aps[i].ch == curCh;
    gfx->setTextColor(mine ? C_GREEN : CHART_COLORS[i % 8]);
    gfx->setCursor(x, y);
    gfx->print(b);
    lx[ln] = x; lw[ln] = bw; ly[ln++] = y;
  }
  // which of 1 / 6 / 11 has the least energy from other networks on it
  const int cands[] = {1, 6, 11};
  float best = 1e9;
  int bestCh = 0;
  for (int k = 0; k < 3; k++) {
    float e = 0;
    for (int i = 0; i < apN; i++) {
      if (cur.length() && cur == aps[i].ssid) continue;
      float c, w;
      apSpan(aps[i], c, w);
      if (fabsf(c - cands[k]) < w + 2.2f) e += powf(10, aps[i].rssi / 10.0f);
    }
    if (e < best) { best = e; bestCh = cands[k]; }
  }
  char b[48];
  if (apN) snprintf(b, sizeof b, "%d APs   least busy: ch %d", apN, bestCh);
  else snprintf(b, sizeof b, "%s", chartScanning ? "scanning..." : "no networks found");
  wuiText(b, 204, RGB565_WHITE, u8g2_font_helvR08_tr);
  if (curCh) {
    snprintf(b, sizeof b, "you: ch %d    tap = back", curCh);
    wuiText(b, 218, C_GREY, u8g2_font_helvR08_tr);
  } else {
    wuiText("tap = back", 218, C_GREY, u8g2_font_helvR08_tr);
  }
}

void drawWifiUi() {
  switch (wui) {
    case WUI_CHART: drawChart(); break;
    case WUI_SCAN: wuiFrame("Wi-Fi"); wuiText("scanning...", 124, C_GREY); break;
    case WUI_LIST: drawWifiList(); break;
    case WUI_CONNECTING: {
      wuiFrame("Wi-Fi");
      wuiText("connecting to", 104, C_GREY);
      char buf[40];
      snprintf(buf, sizeof buf, "%.22s", tryingSsid.c_str());
      wuiText(buf, 130, RGB565_WHITE);
      break;
    }
    case WUI_RESULT: {
      wuiFrame("Wi-Fi");
      char buf[48];
      snprintf(buf, sizeof buf, "%.22s", tryingSsid.c_str());
      wuiText(buf, 104, RGB565_WHITE);
      if (tryOk) {
        wuiText("connected", 130, C_GREEN);
        wuiText(WiFi.localIP().toString().c_str(), 152, C_GREY);
      } else {
        wuiText("failed - back to", 130, C_RED);
        snprintf(buf, sizeof buf, "%.22s", prevSsid.c_str());
        wuiText(buf, 152, C_GREY);
      }
      break;
    }
    case WUI_PORTAL: drawPortal(); break;
  }
}

// ---- actions ----
void startScan() {
  wui = WUI_SCAN;
  WiFi.scanDelete();
  WiFi.scanNetworks(true);
  if (page == PAGE_WIFI) drawWifiUi();
}

// every access point of a finished scan into aps[], strongest first; logged for the PC
void collectAps(int n) {
  apN = 0;
  for (int i = 0; i < n && apN < MAX_APS; i++) {
    wifi_ap_record_t *r = (wifi_ap_record_t *)WiFi.getScanInfoByIndex(i);
    if (!r) continue;
    ApRec &a = aps[apN++];
    snprintf(a.ssid, sizeof a.ssid, "%s", (const char *)r->ssid);
    a.rssi = r->rssi;
    a.ch = r->primary;
    a.second = r->second == WIFI_SECOND_CHAN_ABOVE ? 1 : r->second == WIFI_SECOND_CHAN_BELOW ? -1 : 0;
  }
  for (int i = 1; i < apN; i++)
    for (int j = i; j > 0 && aps[j].rssi > aps[j - 1].rssi; j--) std::swap(aps[j], aps[j - 1]);
  for (int i = 0; i < apN; i++) emit("WIFI ap %d %d %d %s\n", aps[i].ch, aps[i].second, aps[i].rssi, aps[i].ssid);
}

void chartScan() {
  chartScanning = true;
  chartScanMs = millis();
  WiFi.scanDelete();
  WiFi.scanNetworks(true);
}

void collectScan() {
  int n = WiFi.scanComplete();
  if (n < 0) return;  // still running (-1) or failed (-2: shown as empty list)
  collectAps(n);
  rowN = 0;
  for (int i = 0; i < n; i++) {
    String s = WiFi.SSID(i);
    if (!s.length()) continue;
    int r = WiFi.RSSI(i), k = 0;
    for (; k < rowN && rows[k] != s; k++) {}
    if (k < rowN) { if (r > rowRssi[k]) rowRssi[k] = r; continue; }
    if (rowN < WIFI_ROWS) { rows[rowN] = s; rowRssi[rowN++] = r; continue; }
    int weakest = 0;  // full: replace the weakest if this one is stronger
    for (int j = 1; j < rowN; j++) if (rowRssi[j] < rowRssi[weakest]) weakest = j;
    if (r > rowRssi[weakest]) { rows[weakest] = s; rowRssi[weakest] = r; }
  }
  for (int i = 1; i < rowN; i++)  // strongest first
    for (int j = i; j > 0 && rowRssi[j] > rowRssi[j - 1]; j--) { std::swap(rows[j], rows[j - 1]); std::swap(rowRssi[j], rowRssi[j - 1]); }
  WiFi.scanDelete();
  wSel = 0;  // strongest; the connected one if it is in the list
  for (int i = 0; i < rowN; i++) if (WiFi.status() == WL_CONNECTED && WiFi.SSID() == rows[i]) wSel = i;
  wui = WUI_LIST;
  if (page == PAGE_WIFI) drawWifiUi();
}

void tryConnect(const String &ssid, const String &pass, bool save) {
  prevSsid = WiFi.status() == WL_CONNECTED ? WiFi.SSID() : saved[savedLast].ssid;
  int p = findSaved(prevSsid);
  prevPass = p >= 0 ? saved[p].pass : "";
  tryingSsid = ssid; tryingPass = pass; trySave = save;
  trying = true; tryMs = millis();
  emit("WIFI try %s\n", ssid.c_str());
  WiFi.disconnect();
  WiFi.begin(ssid.c_str(), pass.c_str());
  wui = WUI_CONNECTING;
  if (page == PAGE_WIFI) drawWifiUi();
}

void stopPortal() {
  if (!portalOn) return;
  portalOn = false;
  portalDns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
}

const char PORTAL_HTML[] PROGMEM = R"HTML(<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>XIAO Wi-Fi</title><style>body{font-family:system-ui,sans-serif;max-width:360px;margin:32px auto;padding:0 16px;background:#111;color:#eee}
input,button{width:100%;box-sizing:border-box;font-size:17px;padding:10px;margin:6px 0 14px;border-radius:8px;border:1px solid #444;background:#222;color:#eee}
button{background:#d97757;border:0;color:#111;font-weight:700}</style>
<h2>XIAO 小螢幕 Wi-Fi</h2><form method=post action=/wifi>
<label>網路名稱<input name=s value="%SSID%"></label><label>密碼<input name=p type=password autofocus></label>
<button>連線</button></form><p>送出後請看小螢幕上的結果。</p>)HTML";

void startPortal(const String &ssid) {
  const char *digits = "23456789abcdefghjkmnpqrstuvwxyz";  // no 0/o/1/l to misread
  portalPass = "";
  for (int i = 0; i < 8; i++) portalPass += digits[esp_random() % 31];
  portalSsid = ssid;
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP("XIAO-Setup", portalPass.c_str());
  portalDns.start(53, "*", WiFi.softAPIP());
  ensureWeb();
  portalOn = true;
  portalMs = millis();
  wui = WUI_PORTAL;
  emit("WIFI portal for %s\n", ssid.c_str());
  if (page == PAGE_WIFI) drawWifiUi();
}

void setupWifiWeb() {
  otaWeb.on("/wifi", HTTP_GET, []() {
    String h = FPSTR(PORTAL_HTML);
    String s = portalSsid;
    s.replace("\"", "&quot;");
    h.replace("%SSID%", s);
    otaWeb.send(200, "text/html; charset=utf-8", h);
  });
  otaWeb.on("/wifi", HTTP_POST, []() {
    if (!portalOn) { otaWeb.send(403, "text/plain", "setup hotspot is not on"); return; }
    pendingSsid = otaWeb.arg("s");
    pendingPass = otaWeb.arg("p");
    pendingMs = millis();
    otaWeb.send(200, "text/html; charset=utf-8",
                "<meta charset=utf-8><meta name=viewport content=\"width=device-width\"><body style=\"font-family:sans-serif;background:#111;color:#eee;padding:24px\">"
                "<h2>連線中…</h2><p>請看小螢幕。這個熱點會關閉，手機會自動回到原本的網路。</p>");
  });
  otaWeb.onNotFound([]() {  // captive portal: every phone connectivity check lands on the form
    if (portalOn) {
      otaWeb.sendHeader("Location", "http://192.168.4.1/wifi");
      otaWeb.send(302, "text/plain", "");
    } else {
      otaWeb.send(404, "text/plain", "not found");
    }
  });
}

// ---- per-loop work ----
void wifiTick() {
  if (page == PAGE_WIFI && wui == WUI_SCAN) collectScan();
  if (page == PAGE_WIFI && wui == WUI_CHART) {
    if (chartScanning) {
      int n = WiFi.scanComplete();
      if (n >= 0 || millis() - chartScanMs > 8000) {
        chartScanning = false;
        chartScanMs = millis();
        if (n >= 0) collectAps(n);
        WiFi.scanDelete();
        drawChart();
      }
    } else if (millis() - chartScanMs > CHART_SCAN_MS) {
      chartScan();
    }
  }
  if (portalOn) {
    portalDns.processNextRequest();
    if (pendingMs && millis() - pendingMs > 1500) {
      pendingMs = 0;
      stopPortal();
      if (pendingSsid.length()) tryConnect(pendingSsid, pendingPass, true);
    } else if (millis() - portalMs > PORTAL_MS) {
      stopPortal();
      startScan();
    }
  }
  if (trying) {
    if (WiFi.status() == WL_CONNECTED && WiFi.SSID() == tryingSsid) {
      trying = false; tryOk = true;
      if (trySave || findSaved(tryingSsid) >= 0) rememberNet(tryingSsid, tryingPass);
      emit("WIFI ok %s %s\n", tryingSsid.c_str(), WiFi.localIP().toString().c_str());
      wui = WUI_RESULT; wuiMs = millis();
      if (page == PAGE_WIFI) drawWifiUi();
    } else if (millis() - tryMs > 15000) {
      trying = false; tryOk = false;
      emit("WIFI fail %s\n", tryingSsid.c_str());
      WiFi.disconnect();
      WiFi.begin(prevSsid.c_str(), prevPass.c_str());
      wui = WUI_RESULT; wuiMs = millis();
      if (page == PAGE_WIFI) drawWifiUi();
    }
    return;
  }
  if (page == PAGE_WIFI && wui == WUI_RESULT && millis() - wuiMs > 4000) startScan();

  // away from every saved network's place: walk the list
  if (WiFi.status() == WL_CONNECTED || portalOn) {
    offlineSince = 0;
  } else if (!offlineSince) {
    offlineSince = millis();
  } else if (millis() - offlineSince > 20000 && savedN > 1) {
    savedLast = (savedLast + 1) % savedN;
    emit("WIFI roam %s\n", saved[savedLast].ssid.c_str());
    WiFi.disconnect();
    wifiBegin();
    offlineSince = millis();
  }
}

void wifiEnter() {
  page = PAGE_WIFI;
  emit("P %d\n", page);
  startScan();
}

void wifiLeave() {
  stopPortal();
  chartScanning = false;
  WiFi.scanDelete();
}

bool wifiOnRing(int x, int y) {
  int dx = x - 120, dy = y - 120;
  return dx * dx + dy * dy > WR_CENTER * WR_CENTER;
}

// the "channels" button at the top of the centre
bool onChartBtn(int x, int y) { return x >= 76 && x <= 164 && y >= 38 && y <= 68; }

void wifiTouchDown(int x, int y) {
  wifiDrag = wui == WUI_LIST && rowN > 0 && wifiOnRing(x, y);
  chartBtnDown = wui == WUI_LIST && rowN > 0 && onChartBtn(x, y);
  wifiCenterDown = wui == WUI_LIST && rowN > 0 && !wifiOnRing(x, y) && !chartBtnDown;
  if (chartBtnDown) { drawWifiCenter(); return; }
  if (wifiDrag) wifiPick(wifiSectorAt(x, y));
  else if (wifiCenterDown) drawWifiCenter();  // light the hint while pressed
}

void wifiTouchMove(int x, int y) {
  if (wifiDrag) wifiPick(wifiSectorAt(x, y));
}

// release without a swipe
void wifiTap(int x, int y) {
  if (wui == WUI_CHART) {  // any tap: back to the list (no rescan; the chart's scan refreshed it)
    chartScanning = false;
    WiFi.scanDelete();
    wui = WUI_LIST;
    drawWifiUi();
    return;
  }
  if (chartBtnDown) {
    chartBtnDown = false;
    if (onChartBtn(x, y)) {
      wui = WUI_CHART;
      chartScanning = false;
      chartScanMs = millis();  // the list's scan is fresh: draw it now, rescan in 10 s
      drawChart();
    } else {
      drawWifiCenter();
    }
    return;
  }
  bool center = wifiCenterDown && !wifiOnRing(x, y);
  wifiDrag = false;
  if (wifiCenterDown) { wifiCenterDown = false; if (!center && wui == WUI_LIST) drawWifiCenter(); }
  if (!center || wui != WUI_LIST || !rowN) return;
  String ssid = rows[wSel];
  if (WiFi.status() == WL_CONNECTED && WiFi.SSID() == ssid) { drawWifiCenter(); return; }
  int k = findSaved(ssid);
  if (k >= 0) tryConnect(ssid, saved[k].pass, false);
  else startPortal(ssid);
}
