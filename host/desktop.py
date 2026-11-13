# -*- coding: utf-8 -*-
"""Desktop copy of the round display: a tray icon that pops up a round window.

The window is one more "device" for player.py: it receives the same JPEG frames and
sends back the same touch lines the board does (D/M/U/S, P n), so the player and
Claude pages, and the question dial, work from the mouse too. The board's own pages
(door, system status) are drawn by the firmware and are not mirrored here.
"""
import io
import json
import queue
import threading
import time
from pathlib import Path

import clawd
from player import Device, SIZE

PAGES = (0, clawd.PAGE_INDEX)  # player, Claude
SWIPE_PX = 50                  # same as the firmware
BEZEL = 10
KEY = "#010203"                # transparent colour of the window
SETTINGS = Path(__file__).with_name("desktop.json")


class DesktopDevice(Device):
    def __init__(self, log):
        super().__init__()
        self.log = log
        self.name = "desktop"
        self.page = PAGES[0]
        self.visible = False
        self.frame = None
        self.frame_ver = 0
        self.last_q = 0.0
        self.cmds = queue.Queue()  # tray thread -> Tk thread
        try:
            self.cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            self.cfg = {}
        self._scale = self.cfg.get("scale", 1.5)
        threading.Thread(target=self._ui, daemon=True).start()
        threading.Thread(target=self._tray, daemon=True).start()

    @property
    def active(self):
        return self.visible

    # ---- frames from player.py (asyncio thread) ----
    def send_jpeg(self, data, tag=b"G"):
        if tag == b"K":
            return  # dial sector count: only the board draws its own pointer
        now = time.monotonic()
        page = 0 if tag == b"G" else clawd.PAGE_INDEX
        if tag == b"Q":
            # like the board: the first frame of a question pulls the window over
            if now - self.last_q > 2.0 and self.page != page:
                self._set_page(page)
            self.last_q = now
        if page != self.page:
            return
        with self.lock:
            self.frame = data
            self.frame_ver += 1

    def _set_page(self, page):
        self.page = page
        with self.lock:
            self.frame = None
            self.frame_ver += 1
        self.hello.set()  # make player.py draw this page now

    def _touch(self, kind, x=0, y=0):
        with self.lock:
            self.touches.append((kind, int(x), int(y)))

    # ---- tray (own thread) ----
    def _tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception as e:
            self.log.info(f"tray unavailable: {e!r}")
            return
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2, 2, 62, 62), fill=(20, 20, 24, 255), outline=(217, 119, 87, 255), width=6)
        d.ellipse((22, 22, 42, 42), fill=(217, 119, 87, 255))

        def put(cmd):
            return lambda icon, item: self.cmds.put(cmd)

        def scale_item(s):
            return pystray.MenuItem(f"{s:g} 倍", put(("scale", s)), checked=lambda item: self._scale == s, radio=True)

        menu = pystray.Menu(
            pystray.MenuItem("顯示／隱藏小螢幕", put(("toggle",)), default=True),
            pystray.MenuItem("大小", pystray.Menu(*(scale_item(s) for s in (1, 1.5, 2)))),
            pystray.MenuItem("放回右下角", put(("home",))),
        )
        self.icon = pystray.Icon("xiao-display", img, "XIAO 小螢幕", menu)
        self.icon.run()

    # ---- window (own thread; all Tk calls stay here) ----
    def _ui(self):
        import tkinter as tk
        from PIL import Image, ImageDraw, ImageTk

        cfg = self.cfg
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=KEY)
        root.attributes("-transparentcolor", KEY)
        canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        canvas.pack()
        st = {"photo": None, "ver": -1, "drag": None, "move": None, "last_m": 0.0, "pos": cfg.get("pos")}

        def side():
            return int(SIZE * self._scale) + 2 * BEZEL

        def save():
            try:
                SETTINGS.write_text(json.dumps({"scale": self._scale, "pos": st["pos"]}), encoding="utf-8")
            except Exception:
                pass

        def home_pos():
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
            return [r.right - side() - 16, r.bottom - side() - 16]

        def place():
            n = side()
            canvas.config(width=n, height=n)
            x, y = st["pos"] or home_pos()
            root.geometry(f"{n}x{n}+{x}+{y}")

        def compose(jpeg):
            n, s = side(), int(SIZE * self._scale)
            sup = 3  # supersampled masks so the round edges aren't jagged
            out = Image.new("RGB", (n, n), (1, 2, 3))
            ring = Image.new("L", (n * sup, n * sup), 0)
            ImageDraw.Draw(ring).ellipse((0, 0, n * sup - 1, n * sup - 1), fill=255)
            out.paste(Image.new("RGB", (n, n), (24, 24, 28)), (0, 0), ring.resize((n, n), Image.LANCZOS))
            if jpeg:
                scr = Image.open(io.BytesIO(jpeg)).convert("RGB").resize((s, s), Image.LANCZOS)
            else:
                scr = Image.new("RGB", (s, s), (16, 14, 13))
                ImageDraw.Draw(scr).text((s // 2, s // 2), "等待畫面…", fill=(160, 160, 160), anchor="mm",
                                         font=_font(int(16 * self._scale)))
            m = Image.new("L", (s * sup, s * sup), 0)
            ImageDraw.Draw(m).ellipse((0, 0, s * sup - 1, s * sup - 1), fill=255)
            out.paste(scr, (BEZEL, BEZEL), m.resize((s, s), Image.LANCZOS))
            # page dots on the bezel
            d = ImageDraw.Draw(out)
            for i, p in enumerate(PAGES):
                cx, cy = n // 2 - 6 + 12 * i, n - BEZEL // 2 - 1
                col = (217, 119, 87) if p == self.page else (80, 80, 86)
                d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=col)
            return out

        def tick():
            while not self.cmds.empty():
                cmd = self.cmds.get()
                if cmd[0] == "toggle":
                    if self.visible:
                        root.withdraw()
                        self.visible = False
                    else:
                        place()
                        root.deiconify()
                        root.lift()
                        self.visible = True
                        self.hello.set()
                        st["ver"] = -1
                elif cmd[0] == "scale":
                    self._scale = cmd[1]
                    st["ver"] = -1
                    save()
                    place()
                elif cmd[0] == "home":
                    st["pos"] = None
                    save()
                    place()
            if self.visible and st["ver"] != self.frame_ver:
                with self.lock:
                    jpeg, st["ver"] = self.frame, self.frame_ver
                try:
                    st["photo"] = ImageTk.PhotoImage(compose(jpeg))
                    canvas.delete("all")
                    canvas.create_image(0, 0, image=st["photo"], anchor="nw")
                except Exception as e:
                    self.log.info(f"desktop frame error: {e!r}")
            root.after(30, tick)

        def to_screen(e):
            x = (e.x - BEZEL) / self._scale
            y = (e.y - BEZEL) / self._scale
            return x, y

        def on_screen(x, y):
            return (x - 120) ** 2 + (y - 120) ** 2 <= 120 ** 2

        def question_up():
            return self.page == clawd.PAGE_INDEX and time.monotonic() - self.last_q < 1.5

        def down(e):
            x, y = to_screen(e)
            if not on_screen(x, y):  # bezel: drag the window around
                st["move"] = (e.x_root - root.winfo_x(), e.y_root - root.winfo_y())
                return
            dial = question_up() and (x - 120) ** 2 + (y - 120) ** 2 > clawd.DIAL_MIN_R ** 2
            g = st["drag"] = {"x": x, "y": y, "dial": dial, "done": False, "last": (x, y)}
            self._touch("D", x, y)
            if self.page == 0 and (x - 120) ** 2 + (y - 120) ** 2 > 96 ** 2:
                def hold():  # like the board: held still on the progress ring starts the seek drag
                    lx, ly = g["last"]
                    if st["drag"] is g and not g["done"] and abs(lx - g["x"]) <= 12 and abs(ly - g["y"]) <= 12:
                        g["dial"] = True  # stream M from now on, never swipe
                        self._touch("H", lx, ly)
                root.after(400, hold)

        def motion(e):
            if st["move"]:
                ox, oy = st["move"]
                st["pos"] = [e.x_root - ox, e.y_root - oy]
                root.geometry(f"+{st['pos'][0]}+{st['pos'][1]}")
                return
            g = st["drag"]
            if not g:
                return
            x, y = to_screen(e)
            g["last"] = (x, y)
            if g["dial"]:
                if time.monotonic() - st["last_m"] >= 0.04:
                    st["last_m"] = time.monotonic()
                    self._touch("M", x, y)
                return
            dx, dy = x - g["x"], y - g["y"]
            if not g["done"] and abs(dx) > SWIPE_PX and abs(dx) > abs(dy):
                g["done"] = True
                self._touch("S")
                i = PAGES.index(self.page)
                self._set_page(PAGES[(i + (1 if dx < 0 else -1)) % len(PAGES)])
                self._touch_page()

        def up(e):
            if st["move"]:
                st["move"] = None
                save()
                return
            g, st["drag"] = st["drag"], None
            if g and not g["done"]:
                self._touch("U", *g["last"])

        def wheel(e):
            i = PAGES.index(self.page)
            self._set_page(PAGES[(i + (1 if e.delta < 0 else -1)) % len(PAGES)])
            self._touch_page()

        canvas.bind("<ButtonPress-1>", down)
        canvas.bind("<B1-Motion>", motion)
        canvas.bind("<ButtonRelease-1>", up)
        canvas.bind("<MouseWheel>", wheel)
        canvas.bind("<Button-3>", lambda e: self.cmds.put(("toggle",)))
        place()
        root.after(30, tick)
        root.mainloop()

    def _touch_page(self):
        self.log.info(f"desktop page {self.page}")


_fonts = {}


def _font(size):
    from PIL import ImageFont
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(clawd.FONT, size)
    return _fonts[size]
