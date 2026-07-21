"""
A GTA-style circular radio station selector, built on a Tkinter Canvas.

Stations are laid out as wedges around a ring, starting at 12 o'clock and
going clockwise (same as the in-game wheel). Click a wedge to tune to it,
click the center to play/pause.
"""

import colorsys
import math
import tkinter as tk

BG = "#0d0d0d"
TEXT = "#f2f2f2"
MUTED = "#8a8a8a"
ACCENT = "#e63946"
WEDGE_IDLE_ALPHA = "#1c1c1c"

DEFAULT_COLOR_SENTINEL = "#e63946"


def _palette(n):
    """Generate n evenly-spaced hues so the wheel looks lively even
    without custom meta.json colors."""
    colors = []
    for i in range(n):
        h = i / max(n, 1)
        r, g, b = colorsys.hsv_to_rgb(h, 0.55, 0.55)
        colors.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return colors


def _lighten(hex_color, factor=1.25):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    r, g, b = (min(255, int(c * factor)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


class RadioWheel(tk.Canvas):
    def __init__(self, master, stations, on_select=None, on_center_click=None,
                 size=340, **kwargs):
        super().__init__(master, width=size, height=size, bg=BG,
                          highlightthickness=0, cursor="hand2", **kwargs)
        self.stations = stations
        self.on_select = on_select
        self.on_center_click = on_center_click

        self.size = size
        self.center = size / 2
        self.outer_r = size / 2 - 8
        self.inner_r = size * 0.26

        self.selected_index = None
        self.hover_index = None
        self._palette = _palette(len(stations))

        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)

        self._draw()

    # ---------- public API ----------

    def set_selected(self, index):
        self.selected_index = index
        self._draw()

    def set_center_label(self, text):
        self._center_label = text
        self._draw()

    # ---------- geometry ----------

    def _angle_bounds(self, i):
        n = len(self.stations)
        seg = 360 / n
        return i * seg, (i + 1) * seg

    def _point(self, angle_from_top, radius):
        theta = math.radians(angle_from_top - 90)
        return (self.center + radius * math.cos(theta),
                self.center + radius * math.sin(theta))

    def _wedge_polygon(self, angle_start, angle_end, r_inner, r_outer, steps=14):
        pts = []
        for t in range(steps + 1):
            a = angle_start + (angle_end - angle_start) * t / steps
            pts.append(self._point(a, r_outer))
        for t in range(steps + 1):
            a = angle_end - (angle_end - angle_start) * t / steps
            pts.append(self._point(a, r_inner))
        flat = []
        for x, y in pts:
            flat.extend((x, y))
        return flat

    def _index_for_point(self, x, y):
        dx, dy = x - self.center, y - self.center
        dist = math.hypot(dx, dy)
        if dist < self.inner_r or dist > self.outer_r or not self.stations:
            return None
        angle = math.degrees(math.atan2(dy, dx)) + 90  # 0 = top
        angle %= 360
        seg = 360 / len(self.stations)
        return int(angle // seg) % len(self.stations)

    # ---------- events ----------

    def _on_motion(self, event):
        self._set_hover(self._index_for_point(event.x, event.y))

    def _set_hover(self, idx):
        if idx != self.hover_index:
            self.hover_index = idx
            self._draw()

    def _on_click(self, event):
        idx = self._index_for_point(event.x, event.y)
        if idx is not None:
            self.selected_index = idx
            self._draw()
            if self.on_select:
                self.on_select(idx)
            return
        dist = math.hypot(event.x - self.center, event.y - self.center)
        if dist < self.inner_r and self.on_center_click:
            self.on_center_click()

    # ---------- drawing ----------

    def _draw(self):
        self.delete("all")
        n = len(self.stations)

        if n == 0:
            self.create_oval(self.center - self.inner_r, self.center - self.inner_r,
                              self.center + self.inner_r, self.center + self.inner_r,
                              fill="#161616", outline="")
            self.create_text(self.center, self.center, text="No stations",
                              fill=MUTED, font=("Helvetica Neue", 11))
            return

        for i, station in enumerate(self.stations):
            start, end = self._angle_bounds(i)
            base_color = (station.color if station.color != DEFAULT_COLOR_SENTINEL
                          else self._palette[i])

            if i == self.selected_index:
                fill = _lighten(base_color, 1.15)
            elif i == self.hover_index:
                fill = _lighten(base_color, 1.35)
            else:
                fill = base_color

            pad = 1.2  # small gap between wedges
            poly = self._wedge_polygon(start + pad, end - pad,
                                        self.inner_r, self.outer_r)
            outline = "#ffffff" if i == self.selected_index else ""
            width = 2 if i == self.selected_index else 0
            self.create_polygon(*poly, fill=fill, outline=outline, width=width)

            mid = (start + end) / 2
            label_r = (self.inner_r + self.outer_r) / 2
            lx, ly = self._point(mid, label_r)
            name = station.name
            if len(name) > 14:
                # wrap onto two lines around a space near the middle
                words = name.split(" ")
                mid_i = len(words) // 2
                name = " ".join(words[:mid_i]) + "\n" + " ".join(words[mid_i:])
            self.create_text(lx, ly, text=name, fill="white",
                              font=("Helvetica Neue", 9, "bold"),
                              justify="center", width=self.outer_r * 0.7)

        # center hub
        hub_fill = "#161616"
        self.create_oval(self.center - self.inner_r + 4, self.center - self.inner_r + 4,
                          self.center + self.inner_r - 4, self.center + self.inner_r - 4,
                          fill=hub_fill, outline=ACCENT, width=2)

        label = getattr(self, "_center_label", "RADIO")
        self.create_text(self.center, self.center, text=label, fill=TEXT,
                          font=("Helvetica Neue", 12, "bold"), width=self.inner_r * 1.6,
                          justify="center")
