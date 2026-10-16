"""Flat, compact Tk controls matching the supplied dark desktop reference."""
import ctypes
from functools import lru_cache
import math
import os
import tkinter as tk
from tkinter import font as tkfont, ttk

from PIL import Image, ImageDraw, ImageTk

BACKGROUND = '#0d0e10'
PANEL = '#121319'
FIELD = '#191b22'
BORDER = '#292b34'
TEXT = '#eeeef2'
MUTED = '#92959f'
ACCENT = '#d7d9e0'
HOVER = '#292c35'
DISABLED = '#60636d'


@lru_cache(maxsize=64)
def _rounded_image(width, height, radius, fill, outline, stroke):
    # Supersampling keeps small corners and one-pixel borders smooth in Tk.
    scale = 3
    image = Image.new('RGBA', (width * scale, height * scale))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius=radius * scale,
        fill=fill or None, outline=outline or None, width=max(1, round(stroke * scale)))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def rounded(canvas, box, radius=7, **options):
    x1, y1, x2, y2 = box
    width, height = max(1, math.ceil(x2 - x1)), max(1, math.ceil(y2 - y1))
    radius = max(0, min(radius, width / 2, height / 2))
    raster = _rounded_image(width, height, radius, options.get('fill'),
                            options.get('outline'), options.get('width', 1))
    existing = set(canvas.find_all())
    photos = getattr(canvas, '_round_images', [])
    if not any(item in existing for item, _photo in photos):
        photos = []
    photo = ImageTk.PhotoImage(raster, master=canvas)
    item = canvas.create_image(x1, y1, anchor='nw', image=photo)
    photos.append((item, photo))
    canvas._round_images = photos
    return item


def configure_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10), background=BACKGROUND,
                    foreground=TEXT, borderwidth=0)
    style.configure('TFrame', background=BACKGROUND)
    style.configure('Card.TFrame', background=PANEL)
    style.configure('TLabel', background=BACKGROUND, foreground=TEXT)
    style.configure('Card.TLabel', background=PANEL, foreground=TEXT)
    style.configure('Muted.TLabel', foreground=MUTED, font=('Segoe UI', 9))
    style.configure('CardMuted.TLabel', background=PANEL, foreground=MUTED, font=('Segoe UI', 9))
    style.configure('Title.TLabel', font=('Segoe UI', 21, 'bold'))
    style.configure('Heading.TLabel', background=PANEL, font=('Segoe UI', 11, 'bold'))
    for name in ('TEntry', 'TSpinbox', 'TCombobox'):
        style.configure(name, fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                        background=FIELD, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, borderwidth=1, relief='flat', padding=(8, 4),
                        arrowcolor=MUTED, arrowsize=13)
        style.map(name, bordercolor=[('focus', '#555967'), ('disabled', '#24262e')],
                  lightcolor=[('focus', '#555967'), ('disabled', '#24262e')],
                  darkcolor=[('focus', '#555967'), ('disabled', '#24262e')],
                  fieldbackground=[('disabled', PANEL), ('readonly', FIELD)],
                  foreground=[('disabled', DISABLED), ('readonly', TEXT)],
                  arrowcolor=[('disabled', DISABLED), ('active', TEXT)])
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', '#343741')
    root.option_add('*TCombobox*Listbox.selectForeground', TEXT)
    root.option_add('*TCombobox*Listbox.font', ('Segoe UI', 10))


def dark_titlebar(root):
    """Apply the Windows dark title bar to this application's own window."""
    if os.name != 'nt':
        return
    try:
        root.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        handle = user32.GetParent(root.winfo_id())
        enabled = ctypes.c_int(1)
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        for attribute in (20, 19):
            if set_attribute(handle, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)) == 0:
                break
    except (AttributeError, OSError):
        pass


class Card(tk.Frame):
    def __init__(self, parent, padding=14, **kwargs):
        super().__init__(parent, background=BACKGROUND, borderwidth=0, **kwargs)
        self.outline = tk.Canvas(self, background=BACKGROUND, highlightthickness=0, borderwidth=0)
        self.outline.place(x=0, y=0, relwidth=1, relheight=1)
        self.content = ttk.Frame(self, style='Card.TFrame')
        self.content.pack(fill='both', expand=True, padx=padding, pady=padding)
        self.bind('<Configure>', self._draw)

    def _draw(self, _event=None):
        self.outline.delete('all')
        rounded(self.outline, (0.5, 0.5, self.winfo_width() - 0.5, self.winfo_height() - 0.5),
                radius=8, fill=PANEL, outline=BORDER, width=1)


class Button(tk.Canvas):
    def __init__(self, parent, text, command, primary=False, state='normal', **kwargs):
        self.font = tkfont.Font(family='Segoe UI', size=10, weight='bold' if primary else 'normal')
        width = self.font.measure(text) + 24
        background = (ttk.Style(parent).lookup(parent.cget('style') or parent.winfo_class(), 'background')
                      if isinstance(parent, ttk.Widget) else parent.cget('background'))
        super().__init__(parent, width=width, height=32, background=background,
                         highlightthickness=0, borderwidth=0, takefocus=1, **kwargs)
        self.text, self.command, self.primary = text, command, primary
        self.control_state, self.hover, self.pressed = state, False, False
        self.bind('<Configure>', self._draw)
        self.bind('<Enter>', lambda _event: self._hover(True))
        self.bind('<Leave>', lambda _event: self._hover(False))
        self.bind('<Button-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<space>', lambda _event: self.invoke())
        self.bind('<Return>', lambda _event: self.invoke())
        self.bind('<FocusIn>', self._draw)
        self.bind('<FocusOut>', self._draw)
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if 'state' in kwargs:
            self.control_state = kwargs.pop('state')
            self._draw()
        return super().configure(cnf, **kwargs)

    config = configure

    def instate(self, states):
        return all((self.control_state == 'disabled') if state == 'disabled'
                   else (self.control_state != 'disabled') if state == '!disabled' else False
                   for state in states)

    def _hover(self, value):
        self.hover = value
        if not value:
            self.pressed = False
        self._draw()

    def _press(self, _event):
        if self.control_state != 'disabled':
            self.focus_set()
            self.pressed = True
            self._draw()

    def _release(self, event):
        invoke = self.pressed and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self.pressed = False
        self._draw()
        if invoke:
            self.invoke()

    def invoke(self):
        if self.control_state != 'disabled':
            return self.command()

    def _draw(self, _event=None):
        self.delete('all')
        disabled = self.control_state == 'disabled'
        fill = PANEL if disabled else '#f0f0f3' if self.primary and self.hover else ACCENT if self.primary else HOVER if self.hover else '#1e2028'
        text = DISABLED if disabled else BACKGROUND if self.primary else TEXT
        outline = '#555967' if self.focus_get() is self and not disabled else BORDER
        rounded(self, (0.5, 0.5, max(1, self.winfo_width() - 0.5), max(1, self.winfo_height() - 0.5)),
                radius=5, fill=fill, outline=outline, width=1)
        self.create_text(self.winfo_width() / 2, self.winfo_height() / 2, text=self.text, fill=text, font=self.font)
        self.configure(cursor='hand2' if not disabled else '')


class Slider(tk.Canvas):
    def __init__(self, parent, from_, to, variable, command=None):
        super().__init__(parent, height=18, width=120, background=PANEL,
                         highlightthickness=0, borderwidth=0, takefocus=1)
        self.minimum, self.maximum, self.variable, self.command = from_, to, variable, command
        self.control_state = 'normal'
        self.bind('<Configure>', self._draw)
        self.bind('<Button-1>', self._click)
        self.bind('<B1-Motion>', self._click)
        for key, delta in (('Left', -1), ('Down', -1), ('Right', 1), ('Up', 1)):
            self.bind(f'<{key}>', lambda _event, step=delta: self._change(self.variable.get() + step * (self.maximum - self.minimum) / 100))
        self.bind('<Home>', lambda _event: self._change(self.minimum))
        self.bind('<End>', lambda _event: self._change(self.maximum))
        self.bind('<FocusIn>', self._draw)
        self.bind('<FocusOut>', self._draw)
        self.trace = self.variable.trace_add('write', self._draw)
        self.bind('<Destroy>', lambda event: self.variable.trace_remove('write', self.trace) if event.widget is self else None)

    def configure(self, cnf=None, **kwargs):
        if 'state' in kwargs:
            self.control_state = kwargs.pop('state')
            self._draw()
        return super().configure(cnf, **kwargs)

    config = configure

    def _click(self, event):
        if self.control_state != 'disabled':
            self.focus_set()
            self._change(self.minimum + (event.x - 6) / max(1, self.winfo_width() - 12) * (self.maximum - self.minimum))

    def _change(self, value):
        if self.control_state != 'disabled':
            value = min(self.maximum, max(self.minimum, value))
            self.variable.set(value)
            if self.command:
                self.command(str(value))
        return 'break'

    def _draw(self, *_args):
        self.delete('all')
        width, y = max(12, self.winfo_width()), self.winfo_height() / 2
        x = 6 + min(1, max(0, (self.variable.get() - self.minimum) / (self.maximum - self.minimum))) * (width - 12)
        color = DISABLED if self.control_state == 'disabled' else ACCENT
        self.create_line(6, y, width - 6, y, fill=BORDER, width=3, capstyle='round')
        self.create_line(6, y, x, y, fill=color, width=3, capstyle='round')
        self.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color,
                         outline=TEXT if self.focus_get() is self else color)


class Toggle(tk.Canvas):
    def __init__(self, parent, text, variable, command=None, background=PANEL):
        self.font = tkfont.Font(family='Segoe UI', size=9)
        super().__init__(parent, width=self.font.measure(text) + 46, height=27,
                         background=background, highlightthickness=0, borderwidth=0, takefocus=1)
        self.text, self.variable, self.command = text, variable, command
        self.control_state = 'normal'
        self.bind('<Configure>', self._draw)
        self.bind('<Button-1>', lambda _event: self.invoke())
        self.bind('<space>', lambda _event: self.invoke())
        self.bind('<Return>', lambda _event: self.invoke())
        self.bind('<FocusIn>', self._draw)
        self.bind('<FocusOut>', self._draw)
        self.trace = variable.trace_add('write', self._draw)
        self.bind('<Destroy>', lambda event: variable.trace_remove('write', self.trace) if event.widget is self else None)

    def configure(self, cnf=None, **kwargs):
        if 'state' in kwargs:
            self.control_state = kwargs.pop('state')
            self._draw()
        return super().configure(cnf, **kwargs)

    config = configure

    def invoke(self):
        if self.control_state != 'disabled':
            self.focus_set()
            self.variable.set(not self.variable.get())
            if self.command:
                self.command()
        return 'break'

    def _draw(self, *_args):
        self.delete('all')
        disabled = self.control_state == 'disabled'
        active = self.variable.get()
        y = self.winfo_height() / 2
        rounded(self, (0.5, y - 7, 29.5, y + 7), radius=7,
                fill=DISABLED if disabled and active else ACCENT if active else FIELD,
                outline='#555967' if self.focus_get() is self else BORDER)
        x = 22 if active else 8
        self.create_oval(x - 4.5, y - 4.5, x + 4.5, y + 4.5, fill=PANEL if active else MUTED, outline='')
        self.create_text(40, y, text=self.text, font=self.font, anchor='w', fill=DISABLED if disabled else TEXT)


class Segments(tk.Canvas):
    def __init__(self, parent, values, variable, command=None, width=None):
        self.font = tkfont.Font(family='Segoe UI', size=9)
        self.values, self.variable, self.command = values, variable, command
        width = width or sum(self.font.measure(value) + 22 for value in values)
        super().__init__(parent, width=width, height=30, background=PANEL,
                         highlightthickness=0, borderwidth=0, takefocus=1)
        self.control_state = 'normal'
        self.bind('<Configure>', self._draw)
        self.bind('<Button-1>', self._click)
        self.bind('<Left>', lambda _event: self._step(-1))
        self.bind('<Right>', lambda _event: self._step(1))
        self.bind('<FocusIn>', self._draw)
        self.bind('<FocusOut>', self._draw)
        self.trace = variable.trace_add('write', self._draw)
        self.bind('<Destroy>', lambda event: variable.trace_remove('write', self.trace) if event.widget is self else None)

    def configure(self, cnf=None, **kwargs):
        if 'state' in kwargs:
            self.control_state = kwargs.pop('state')
            self._draw()
        return super().configure(cnf, **kwargs)

    config = configure

    def _select(self, index):
        if self.control_state != 'disabled':
            self.focus_set()
            self.variable.set(self.values[min(len(self.values) - 1, max(0, index))])
            if self.command:
                self.command()
        return 'break'

    def _click(self, event):
        return self._select(int(event.x / max(1, self.winfo_width()) * len(self.values)))

    def _step(self, delta):
        return self._select(self.values.index(self.variable.get()) + delta)

    def _draw(self, *_args):
        self.delete('all')
        width, height = self.winfo_width(), self.winfo_height()
        rounded(self, (0.5, 0.5, max(1, width - 0.5), max(1, height - 0.5)), radius=5,
                fill='#0f1015', outline='#555967' if self.focus_get() is self else BORDER)
        segment = width / len(self.values)
        for index, value in enumerate(self.values):
            selected = value == self.variable.get()
            if selected:
                rounded(self, (index * segment + 3, 3, (index + 1) * segment - 3, height - 3),
                        radius=4, fill='#2b2e37', outline='')
            self.create_text((index + .5) * segment, height / 2, text=value, font=self.font,
                             fill=DISABLED if self.control_state == 'disabled' else TEXT if selected else MUTED)


class Progress(tk.Canvas):
    def __init__(self, parent, variable):
        super().__init__(parent, height=3, background='#24262e', highlightthickness=0, borderwidth=0)
        self.variable = variable
        self.bind('<Configure>', self._draw)
        self.trace = variable.trace_add('write', self._draw)
        self.bind('<Destroy>', lambda event: variable.trace_remove('write', self.trace) if event.widget is self else None)

    def _draw(self, *_args):
        self.delete('all')
        width = self.winfo_width() * min(1, max(0, self.variable.get() / 100))
        self.create_rectangle(0, 0, width, self.winfo_height(), fill=ACCENT, outline='')


class Scrollbar(tk.Canvas):
    """Thin scrollbar with page clicks, dragging, and the standard Tk set API."""
    def __init__(self, parent, command):
        super().__init__(parent, width=6, background=PANEL, highlightthickness=0, borderwidth=0)
        self.command = command
        self.first, self.last, self.grab_offset = 0, 1, 0
        self.dragging = False
        self.hover = False
        self.bind('<Configure>', self._draw)
        self.bind('<Button-1>', self._press)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Enter>', lambda _event: self._hover(True))
        self.bind('<Leave>', lambda _event: self._hover(False))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _bounds(self):
        height = max(1, self.winfo_height())
        length = min(height, max(28, (self.last - self.first) * height))
        top = self.first / max(.0001, 1 - (self.last - self.first)) * (height - length)
        return top, length

    def _press(self, event):
        top, length = self._bounds()
        if top <= event.y <= top + length:
            self.dragging = True
            self.grab_offset = event.y - top
        elif self.last - self.first < 1:
            self.command('scroll', -1 if event.y < top else 1, 'pages')
        self._draw()

    def _drag(self, event):
        if self.dragging:
            _top, length = self._bounds()
            fraction = (event.y - self.grab_offset) / max(1, self.winfo_height() - length)
            self.command('moveto', min(1, max(0, fraction)) * (1 - (self.last - self.first)))

    def _release(self, _event):
        self.dragging = False
        self._draw()

    def _hover(self, value):
        self.hover = value
        self._draw()

    def _draw(self, _event=None):
        self.delete('all')
        if self.last - self.first < .999:
            top, length = self._bounds()
            rounded(self, (0, top, self.winfo_width(), top + length), radius=3,
                    fill='#575b68' if self.hover or self.dragging else '#353842', outline='')
