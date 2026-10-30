"""Small Tk building blocks of the desktop app: rounded cards and the status dot icon."""
from __future__ import annotations

import tkinter as tk


def rounded_rect_points(x1: float, y1: float, x2: float, y2: float, radius: float) -> list[float]:
    """Polygon points that Tk's `smooth=True` turns into a rectangle with rounded corners."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class Card:
    """A fixed-width rounded panel. Put widgets in `body`, then call `layout()` to fit its height.

    Tk sends no <Configure> for a window item outside the canvas's visible area, so the owner calls
    `layout()` after changing the content instead of relying on an event binding.
    """

    def __init__(self, parent: tk.Misc, *, width: int, pad_x: int, pad_y: int, gap: int, radius: int,
                 fill: str, outline: str, behind: str) -> None:
        self.width, self.pad_x, self.pad_y, self.radius = width, pad_x, pad_y, radius
        self.fill, self.outline = fill, outline
        self.canvas = tk.Canvas(parent, width=width, height=1, bg=behind, highlightthickness=0, bd=0)
        self.canvas.pack(pady=(0, gap))
        self.body = tk.Frame(self.canvas, bg=fill)
        self._item = self.canvas.create_window(pad_x, pad_y, window=self.body, anchor="nw", width=width - 2 * pad_x)

    def layout(self, _event: object = None) -> None:
        height = self.body.winfo_reqheight() + 2 * self.pad_y
        if int(self.canvas.cget("height")) != height:
            self.canvas.configure(height=height)
        self.canvas.delete("shape")
        points = rounded_rect_points(0, 0, self.width - 1, height - 1, self.radius)
        self.canvas.create_polygon(points, smooth=True, fill=self.fill, outline=self.outline, tags="shape")
        self.canvas.tag_lower("shape")


def dot_icon(master: tk.Misc, color: str, size: int) -> tk.PhotoImage:
    """A filled circle on a transparent square."""
    image = tk.PhotoImage(master=master, width=size, height=size)
    center, radius = size / 2, size / 2 - 1
    for y in range(size):
        span = [x for x in range(size) if (x + 0.5 - center) ** 2 + (y + 0.5 - center) ** 2 <= radius ** 2]
        if span:
            image.put(color, to=(span[0], y, span[-1] + 1, y + 1))
    return image
