// Night mode (page PAGE_NIGHT, opened by tapping the bottom of the system page).
// Inside the night window the backlight goes off once the screen has been untouched for
// NIGHT_WAKE_MS; a tap only wakes it (that touch does nothing else). The PC is told with
// "N <in window> <lit>" so it stops sending frames while dark and sends questions to TG instead.
// Unknown time (no NTP yet) never counts as night. Settings live in NVS ("night").
// Included after wifi_ui.h: reuses wuiText / wuiFrame / C_PICK / C_GREY.

#define PAGE_NIGHT 7
#define NIGHT_WAKE_MS 30000UL
#define NR_OUT 119
#define NR_IN 101
#define C_NIGHT 0x3B3F  // the night span on the 24 h ring
#define C_TRACK 0x2126

Preferences nightPrefs;
int nightStart = 23, nightEnd = 8;  // whole hours, local time
bool nightOn = true;
bool nightNow = false;              // inside the window right now
bool screenDark = false;
bool swallowTouch = false;          // the touch that woke the screen
int nightSel = 0;                   // 0 = editing the start, 1 = the end
bool nightDrag = false;             // this touch started on the ring: it sets the hour, never swipes
unsigned long nightCheckMs = 0, nightEmitMs = 0;
int nightDrawnMin = -1;

void loadNight() {
  nightPrefs.begin("night", false);
  nightStart = nightPrefs.getInt("start", 23);
  nightEnd = nightPrefs.getInt("end", 8);
  nightOn = nightPrefs.getBool("on", true);
}

void saveNight() {
  nightPrefs.putInt("start", nightStart);
  nightPrefs.putInt("end", nightEnd);
  nightPrefs.putBool("on", nightOn);
}

bool localNow(struct tm &t) {
  time_t now = time(nullptr);
  localtime_r(&now, &t);
  return t.tm_year + 1900 >= 2024;  // before the first NTP sync the clock sits in 1970
}

bool inNightWindow(int h) {
  if (!nightOn || nightStart == nightEnd) return false;
  return nightStart < nightEnd ? (h >= nightStart && h < nightEnd) : (h >= nightStart || h < nightEnd);
}

void emitNight() {
  emit("N %d %d\n", nightNow ? 1 : 0, screenDark ? 0 : 1);
  nightEmitMs = millis();
}

void setDark(bool dark) {
  if (dark == screenDark) return;
  screenDark = dark;
  digitalWrite(TFT_BL, dark ? LOW : HIGH);
  emitNight();
}

// a touch arrived; true = it only woke the screen and must not reach the page
bool nightWake() {
  if (!screenDark) return false;
  setDark(false);
  return true;
}

// ---- drawing ----
// hour h on the ring: midnight at 12 o'clock, clockwise; fillArc counts degrees from 3 o'clock
float hourDeg(float h) { return fmodf(h * 15 + 270, 360); }

void ringArc(float h0, float h1, uint16_t color) {
  float a0 = hourDeg(h0), a1 = a0 + fmodf(h1 - h0 + 24, 24) * 15;
  if (a1 > 360) {
    gfx->fillArc(120, 120, NR_OUT, NR_IN, a0, 359.9, color);
    gfx->fillArc(120, 120, NR_OUT, NR_IN, 0, a1 - 360, color);
  } else {
    gfx->fillArc(120, 120, NR_OUT, NR_IN, a0, a1, color);
  }
}

void drawNightRing() {
  gfx->fillArc(120, 120, NR_IN - 1, 89, 0, 359.9, SYS_BG);  // the old "now" dot
  gfx->fillArc(120, 120, NR_OUT, NR_IN, 0, 359.9, C_TRACK);
  if (nightOn && nightStart != nightEnd) ringArc(nightStart, nightEnd, C_NIGHT);
  // the hour being edited: a yellow tick on its edge of the span
  float h = nightSel == 0 ? nightStart : nightEnd;
  ringArc(h - 0.15f, h + 0.15f, C_PICK);
  struct tm t;
  if (localNow(t)) {  // now: a white dot just inside the ring
    float a = (t.tm_hour + t.tm_min / 60.0f) * 15 * PI / 180;
    gfx->fillCircle(120 + sinf(a) * 94, 120 - cosf(a) * 94, 3, RGB565_WHITE);
  }
  const char *marks[] = {"0", "6", "12", "18"};
  for (int i = 0; i < 4; i++) {
    float a = i * 90 * PI / 180;
    gfx->setFont(u8g2_font_helvR08_tr);
    gfx->setTextColor(C_GREY);
    int16_t bx, by;
    uint16_t bw, bh;
    gfx->getTextBounds(marks[i], 0, 0, &bx, &by, &bw, &bh);
    gfx->setCursor(120 + sinf(a) * 84 - bw / 2, 120 - cosf(a) * 84 + 4);
    gfx->print(marks[i]);
  }
}

void drawNightClock() {
  gfx->fillRect(66, 78, 108, 26, SYS_BG);
  struct tm t;
  char buf[12];
  if (localNow(t)) snprintf(buf, sizeof buf, "%02d:%02d", t.tm_hour, t.tm_min);
  else snprintf(buf, sizeof buf, "--:--");
  wuiText(buf, 100, RGB565_WHITE, u8g2_font_helvB18_tr);
  nightDrawnMin = localNow(t) ? t.tm_hour * 60 + t.tm_min : -1;
}

void drawNightValues() {
  gfx->fillRect(44, 122, 152, 76, SYS_BG);
  char buf[8];
  gfx->setFont(u8g2_font_helvB14_tr);
  for (int i = 0; i < 2; i++) {
    snprintf(buf, sizeof buf, "%02d:00", i == 0 ? nightStart : nightEnd);
    int16_t bx, by;
    uint16_t bw, bh;
    gfx->getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
    int cx = i == 0 ? 80 : 160;
    gfx->setTextColor(!nightOn ? C_GREY : nightSel == i ? C_PICK : RGB565_WHITE);
    gfx->setCursor(cx - bw / 2, 142);
    gfx->print(buf);
    gfx->setFont(u8g2_font_helvR08_tr);
    gfx->setTextColor(C_GREY);
    const char *cap = i == 0 ? "sleep" : "wake";
    gfx->getTextBounds(cap, 0, 0, &bx, &by, &bw, &bh);
    gfx->setCursor(cx - bw / 2, 156);
    gfx->print(cap);
    gfx->setFont(u8g2_font_helvB14_tr);
  }
  wuiText("-", 140, C_GREY, u8g2_font_helvB14_tr);
  gfx->drawRoundRect(94, 170, 52, 22, 11, nightOn ? C_GREEN : C_GREY);
  wuiText(nightOn ? "ON" : "OFF", 186, nightOn ? C_GREEN : C_GREY, u8g2_font_helvB10_tr);
}

void drawNightUi() {
  wuiFrame("");
  wuiText("Night mode", 62, 0x9CF3, u8g2_font_helvB10_tr);
  drawNightRing();
  drawNightClock();
  drawNightValues();
}

// ---- touch ----
bool nightOnRing(int x, int y) {
  int dx = x - 120, dy = y - 120;
  return dx * dx + dy * dy > 92 * 92;
}

void nightSetFrom(int x, int y) {
  float a = atan2f(x - 120, -(y - 120)) * 180 / PI;  // 0 at 12 o'clock, clockwise
  if (a < 0) a += 360;
  int h = ((int)roundf(a / 15)) % 24;
  int &v = nightSel == 0 ? nightStart : nightEnd;
  if (h == v) return;
  v = h;
  drawNightRing();
  drawNightValues();
}

void nightTouchDown(int x, int y) {
  nightDrag = nightOn && nightOnRing(x, y);
  if (nightDrag) nightSetFrom(x, y);
}

void nightTouchMove(int x, int y) {
  if (nightDrag) nightSetFrom(x, y);
}

void nightTap(int x, int y) {
  if (nightDrag) { nightDrag = false; saveNight(); return; }
  if (y >= 166 && y <= 198 && x >= 84 && x <= 156) {
    nightOn = !nightOn;
  } else if (y >= 118 && y < 166 && nightOn) {
    nightSel = x < 120 ? 0 : 1;
  } else {
    return;
  }
  saveNight();
  drawNightRing();
  drawNightValues();
}

void nightEnter() {
  page = PAGE_NIGHT;
  emit("P %d\n", page);
  drawNightUi();
}

// the line under the uptime on the system page
void drawNightLine() {
  gfx->fillRect(60, 204, 120, 12, SYS_BG);
  char buf[32];
  if (nightOn && nightStart != nightEnd) snprintf(buf, sizeof buf, "night %02d:00-%02d:00  >", nightStart, nightEnd);
  else snprintf(buf, sizeof buf, "night off  >");
  wuiText(buf, 214, C_GREY, u8g2_font_helvR08_tr);
}

void nightTick() {
  if (millis() - nightCheckMs < 500) return;
  nightCheckMs = millis();
  struct tm t;
  bool known = localNow(t);
  bool now = known && inNightWindow(t.tm_hour);
  if (now != nightNow) {
    nightNow = now;
    emitNight();
  }
  setDark(nightNow && !touching && millis() - lastTouchMs > NIGHT_WAKE_MS);
  if (millis() - nightEmitMs > 10000) emitNight();  // a PC on USB serial has no connect event
  if (page == PAGE_NIGHT && !screenDark && known && t.tm_hour * 60 + t.tm_min != nightDrawnMin) {
    drawNightClock();
    drawNightRing();
  }
}
