"""Scrollable result thumbnails and read-only generation details."""
from collections import OrderedDict
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk

from theme import (PANEL, FIELD, BORDER, TEXT, MUTED, ACCENT, Button, Card,
                   Segments, Toggle, Scrollbar, rounded)


class HistoryPanel(Card):
    ROW_HEIGHT = 182

    def __init__(self, parent, scope, show_details, on_select, on_scope, on_hide,
                 on_refresh, on_details):
        super().__init__(parent, width=264, padding=12)
        self.pack_propagate(False)
        self.records = []
        self.selected = None
        self.enabled = True
        self.photos = []
        self.cache = OrderedDict()
        self.scope, self.show_details = scope, show_details
        self.on_select, self.on_details = on_select, on_details
        content = self.content
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)
        header = ttk.Frame(content, style='Card.TFrame')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 9))
        ttk.Label(header, text='История', style='Heading.TLabel').pack(side='left')
        Button(header, text='Скрыть', command=on_hide).pack(side='right')
        self.tabs = Segments(content, values=['Все', 'Текущий файл'], variable=scope,
                             command=on_scope, width=230)
        self.tabs.grid(row=1, column=0, sticky='ew')
        count_row = ttk.Frame(content, style='Card.TFrame')
        count_row.grid(row=2, column=0, sticky='ew', pady=(7, 6))
        self.count = tk.StringVar(value='Нет генераций')
        ttk.Label(count_row, textvariable=self.count, style='CardMuted.TLabel').pack(side='left')
        Button(count_row, text='Обновить', command=on_refresh).pack(side='right')
        list_frame = ttk.Frame(content, style='Card.TFrame')
        list_frame.grid(row=3, column=0, sticky='nsew')
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(list_frame, background=PANEL, borderwidth=0,
                                highlightthickness=0, width=224, height=120, takefocus=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = Scrollbar(list_frame, command=self._scroll)
        self.scrollbar.grid(row=0, column=1, sticky='ns', padx=(3, 0))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind('<Configure>', lambda _event: self.draw())
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Button-1>', self._click)
        self.canvas.bind('<Return>', lambda _event: self._activate_selected())
        self.canvas.bind('<Up>', lambda _event: self._keyboard(-1))
        self.canvas.bind('<Down>', lambda _event: self._keyboard(1))
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Leave>', lambda _event: self.canvas.configure(cursor=''))
        self.details_toggle = Toggle(content, text='Параметры результата', variable=show_details,
                                     command=self.toggle_details)
        self.details_toggle.grid(row=4, column=0, sticky='w', pady=(9, 0))
        self.details_frame = ttk.Frame(content, style='Card.TFrame')
        self.details_frame.grid(row=5, column=0, sticky='ew', pady=(5, 0))
        self.date = tk.StringVar(value='Выберите готовую генерацию')
        self.detail_text = tk.StringVar()
        ttk.Label(self.details_frame, textvariable=self.date, style='CardMuted.TLabel',
                  wraplength=230).pack(anchor='w', fill='x', pady=(0, 5))
        ttk.Label(self.details_frame, textvariable=self.detail_text, style='Card.TLabel',
                  wraplength=230, justify='left', font=('Segoe UI', 9)).pack(anchor='w', fill='x')
        self.toggle_details(notify=False)

    def toggle_details(self, notify=True):
        self.details_frame.grid() if self.show_details.get() else self.details_frame.grid_remove()
        if notify:
            self.on_details()

    def set_records(self, records, reset_scroll=False):
        self.records = records
        self.count.set(f'Генераций: {len(records)}' if records else 'Нет генераций')
        if reset_scroll:
            self.canvas.yview_moveto(0)
        self.draw()

    def set_selected(self, record):
        self.selected = record.key if record else None
        self.date.set(f'Дата: {record.date_label}' if record else 'Выберите готовую генерацию')
        self.detail_text.set(record.details() if record else '')
        self.draw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.canvas.configure(cursor='')

    def reveal_selected(self):
        index = next((i for i, record in enumerate(self.records) if record.key == self.selected), None)
        if index is None:
            return
        top, height = self.canvas.canvasy(0), self.canvas.winfo_height()
        start, end = index * self.ROW_HEIGHT, (index + 1) * self.ROW_HEIGHT
        if start < top or end > top + height:
            target = start if start < top or height < self.ROW_HEIGHT else end - height
            self.canvas.yview_moveto(target / max(1, len(self.records) * self.ROW_HEIGHT))
            self.draw()

    def _scroll(self, *args):
        self.canvas.yview(*args)
        self.draw()

    def _wheel(self, event):
        if event.delta:
            self._scroll('scroll', -int(event.delta / 120), 'units')
        return 'break'

    def _index_at(self, event):
        index = int(self.canvas.canvasy(event.y) // self.ROW_HEIGHT)
        return index if 0 <= index < len(self.records) else None

    def _hover(self, event):
        self.canvas.configure(cursor='hand2' if self.enabled and self._index_at(event) is not None else '')

    def _click(self, event):
        self.canvas.focus_set()
        index = self._index_at(event)
        if self.enabled and index is not None:
            self.on_select(self.records[index])
        return 'break'

    def _activate_selected(self):
        if self.enabled:
            record = next((item for item in self.records if item.key == self.selected), None)
            if record:
                self.on_select(record)
        return 'break'

    def _keyboard(self, direction):
        if self.enabled and self.records:
            index = next((i for i, item in enumerate(self.records) if item.key == self.selected), -1)
            index = min(len(self.records) - 1, max(0, index + direction))
            self.on_select(self.records[index])
            self.canvas.yview_moveto(index * self.ROW_HEIGHT / max(1, len(self.records) * self.ROW_HEIGHT))
            self.draw()
        return 'break'

    def _thumbnail(self, record):
        if record.key in self.cache:
            self.cache.move_to_end(record.key)
            return self.cache[record.key]
        try:
            preview = record.folder / 'thumbnail.png'
            comparison = record.folder / 'comparison.png'
            if preview.is_file():
                with Image.open(preview) as image:
                    image = image.convert('RGB')
            elif comparison.is_file():
                # The original worker's compact comparison sheet contains a result preview.
                with Image.open(comparison) as sheet:
                    width = (sheet.width - 48) // 2
                    if width <= 0 or sheet.height <= 70:
                        raise ValueError('Invalid comparison sheet')
                    image = sheet.crop((32 + width, 54, 32 + 2 * width, sheet.height - 16)).convert('RGB')
            else:
                with Image.open(record.folder / 'output.png') as image:
                    image = image.convert('RGB')
            image.thumbnail((236, 106), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            image = Image.new('RGB', (160, 90), FIELD)
        self.cache[record.key] = image
        while len(self.cache) > 60:
            self.cache.popitem(last=False)
        return image

    def draw(self):
        canvas = self.canvas
        canvas.delete('all')
        self.photos = []
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        canvas.configure(scrollregion=(0, 0, width, max(height, len(self.records) * self.ROW_HEIGHT)))
        if not self.records:
            message = 'Для этого файла\nготовых генераций пока нет' if self.scope.get() == 'Текущий файл' else 'Готовые генерации\nпоявятся здесь'
            canvas.create_text(width / 2, min(height / 2, 100), text=message, width=max(20, width - 18),
                               fill=MUTED, font=('Segoe UI', 9), justify='center')
            return
        first = max(0, int(canvas.canvasy(0) // self.ROW_HEIGHT))
        last = min(len(self.records), first + height // self.ROW_HEIGHT + 2)
        for index in range(first, last):
            record = self.records[index]
            y = index * self.ROW_HEIGHT
            rounded(canvas, (1, y + 1, width - 1, y + self.ROW_HEIGHT - 7), radius=6,
                    fill=FIELD, outline=ACCENT if record.key == self.selected else BORDER, width=1)
            image = ImageOps.contain(self._thumbnail(record), (max(1, width - 14), 106))
            photo = ImageTk.PhotoImage(image, master=canvas)
            self.photos.append(photo)
            canvas.create_image(width / 2, y + 59, image=photo)
            title = record.source_name
            if len(title) > 30:
                title = title[:27] + '…'
            canvas.create_text(9, y + 118, text=title, anchor='nw', fill=TEXT, font=('Segoe UI', 9))
            canvas.create_text(9, y + 137, text=record.date_label, anchor='nw', fill=MUTED, font=('Segoe UI', 8))
            canvas.create_text(9, y + 153, text=f'{record.size_label} · стиль {record.controls.get("style", "—")}',
                               anchor='nw', fill=MUTED, font=('Segoe UI', 8))
