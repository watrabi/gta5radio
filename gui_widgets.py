"""Small reusable Tkinter widgets for the radio's GUI."""

import tkinter as tk


def fmt_time(seconds):
    """12.4 -> '0:12', 341 -> '5:41'."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class ProgressBar(tk.Canvas):
    """A slim rounded progress bar. set_fraction(0..1) redraws it;
    click-free -- this mirrors the "always-on broadcast" model where
    you can't scrub a live station, only see how far into the current
    clip it is."""

    def __init__(self, master, width=390, height=7, bg="#0d0d0d",
                 trough="#262626", fill="#e63946", **kwargs):
        super().__init__(master, width=width, height=height, bg=bg,
                          highlightthickness=0, **kwargs)
        self.width = width
        self.height = height
        self.trough_color = trough
        self.fill_color = fill
        self._fraction = 0.0
        self._draw()

    def set_colors(self, fill=None, trough=None):
        if fill:
            self.fill_color = fill
        if trough:
            self.trough_color = trough
        self._draw()

    def set_fraction(self, frac):
        frac = 0.0 if frac != frac else max(0.0, min(1.0, frac))  # guard NaN
        self._fraction = frac
        self._draw()

    def _draw(self):
        self.delete("all")
        r = self.height / 2
        self._rounded_rect(0, 0, self.width, self.height, r, fill=self.trough_color)
        fw = self.width * self._fraction
        if fw > self.height:  # only draw once it's wide enough to have rounded ends
            self._rounded_rect(0, 0, fw, self.height, r, fill=self.fill_color)
        elif fw > 0:
            self.create_oval(0, 0, self.height, self.height, fill=self.fill_color, width=0)

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        if r <= 0.5:
            self.create_rectangle(x1, y1, x2, y2, width=0, **kw)
            return
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(pts, smooth=True, width=0, **kw)


class HoverButton(tk.Button):
    """A tk.Button that swaps to a highlight color on hover, with a
    pointer cursor -- makes the flat-color buttons feel more clickable."""

    def __init__(self, master, hover_bg=None, **kwargs):
        super().__init__(master, cursor="hand2", **kwargs)
        self._normal_bg = kwargs.get("bg", self["background"])
        self._hover_bg = hover_bg or self["activebackground"]
        self.bind("<Enter>", lambda e: self.config(bg=self._hover_bg))
        self.bind("<Leave>", lambda e: self.config(bg=self._normal_bg))
