"""Native Windows interface for the existing DLSSNR PyTorch environment."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback

from PIL import Image, ImageOps, ImageTk

from config import (DEFAULT_CONTROLS, DEFAULT_DENOISE, DEFAULT_OUTPUT, JobConfig, PREFERENCES,
                    RESOLUTIONS, ROOT, NATIVE_MODE, clean_path, create_job, load_preferences,
                    target_size, write_json)
from theme import (BACKGROUND, PANEL, TEXT, MUTED, ACCENT, Button, Card, Slider,
                   Toggle, Segments, Progress, Scrollbar, configure_theme, dark_titlebar)
from viewport import ImageViewport
from history import for_source, read_generation, scan_history, source_key
from history_panel import HistoryPanel


class NumericControl:
    def __init__(self, parent, label, value, minimum=0, maximum=2):
        self.minimum, self.maximum = minimum, maximum
        self.frame = ttk.Frame(parent, style='Card.TFrame')
        self.frame.columnconfigure(0, weight=1)
        ttk.Label(self.frame, text=label, style='Card.TLabel').grid(row=0, column=0, sticky='w')
        self.text = tk.StringVar(value=str(value))
        self.slider = tk.DoubleVar(value=value)
        self.entry = ttk.Entry(self.frame, width=5, textvariable=self.text, justify='right')
        self.entry.grid(row=0, column=1, sticky='e')
        self.scale = Slider(self.frame, from_=minimum, to=maximum,
                            variable=self.slider, command=self.sync_slider)
        self.scale.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(5, 0))
        self.entry.bind('<FocusOut>', lambda _event: self.sync_entry())
        self.entry.bind('<Return>', lambda _event: self.sync_entry())

    def sync_slider(self, value):
        self.text.set(f'{float(value):.2f}'.rstrip('0').rstrip('.'))

    def sync_entry(self):
        try:
            value = self.get()
            if self.minimum <= value <= self.maximum:
                self.slider.set(value)
        except ValueError:
            pass

    def get(self):
        return float(self.text.get().strip().replace(',', '.'))

    def set(self, value):
        self.text.set(str(value))
        self.slider.set(value)

    def enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        self.entry.configure(state=state)
        self.scale.configure(state=state)


class Application:
    def __init__(self, root, restore=True, persist=True):
        self.root = root
        self.persist = persist
        self.process = None
        self.events = queue.Queue()
        self.current_folder = None
        self.result_folder = None
        self.selected_generation = None
        self.history_records = []
        self.completed = False
        self.cancel_requested = False
        self.closing = False
        self.started = 0
        self.source_size = None
        self.loaded_path = None
        self.preview_original = self.preview_result = None
        self.rendered_original = self.rendered_result = None
        self.viewport = None
        self.preview_photos = []
        self.resize_after = None
        self.wipe_dragging = False
        self.wipe_grab_offset = 0
        self.pan_dragging = False
        self.pan_last = (0, 0)
        self.editable_widgets = []
        preferences = load_preferences() if restore else {}
        stored_roots = preferences.get('history_roots', [])
        self.history_roots = [str(DEFAULT_OUTPUT)] + ([path for path in stored_roots if isinstance(path, str)]
                                                     if isinstance(stored_roots, list) else [])
        self.show_history = tk.BooleanVar(value=preferences.get('show_history', True))
        self.show_result_settings = tk.BooleanVar(value=preferences.get('show_result_settings', True))
        scope = preferences.get('history_scope', 'Все')
        self.history_scope = tk.StringVar(value=scope if scope in ('Все', 'Текущий файл') else 'Все')
        saved_controls = preferences.get('controls', {})
        controls = DEFAULT_CONTROLS | (saved_controls if isinstance(saved_controls, dict) else {})
        self.source = tk.StringVar(value=preferences.get('source', '' if NATIVE_MODE else str(ROOT / 'Test/input/Cam_1.jpg')))
        self.output_base = tk.StringVar(value=preferences.get('output_base', str(DEFAULT_OUTPUT)))
        self.resolution = tk.StringVar(value=next((name for name, value in RESOLUTIONS.items()
                                                  if value == preferences.get('long_edge', 4096 if NATIVE_MODE else 6144)), 'Свой размер'))
        self.custom_edge = tk.StringVar(value=str(preferences.get('long_edge', 4096 if NATIVE_MODE else 6144) or 6144))
        self.style = tk.StringVar(value=str(controls['style']))
        self.seed = tk.StringVar(value=str(controls['seed']))
        self.auto_mask = tk.BooleanVar(value=controls['auto_mask'])
        self.skin_link = tk.BooleanVar(value=controls['skin_structure'] < 0)
        self.device = tk.StringVar(value='GPU (CUDA)' if preferences.get('device', 'cuda') == 'cuda' else 'CPU')
        self.denoise_enabled = tk.BooleanVar(value=preferences.get('denoise_enabled', DEFAULT_DENOISE['enabled']))
        self.denoise_backend = tk.StringVar(value='Intel OIDN' if preferences.get('denoise_backend') == 'oidn' else 'OptiX 3.0')
        self.denoise_position = tk.StringVar(value='После DLSSNR' if preferences.get('denoise_position') == 'after' else 'До DLSSNR')
        self.denoise_strength_value = preferences.get('denoise_strength', DEFAULT_DENOISE['strength'])
        self.preview_mode = tk.StringVar(value='Сравнить')
        self.wipe = tk.DoubleVar(value=50)
        self.compare_hint = tk.StringVar(value='Сравнение доступно после обработки')
        self.zoom_info = tk.StringVar(value='—')
        self.show_log = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='Выберите файл, настройте параметры и нажмите «Обработать».')
        self.dimensions = tk.StringVar(value='Размер с сохранением пропорций')
        self.elapsed = tk.StringVar(value='')
        self.result_info = tk.StringVar(value='Предпросмотр изображения')
        self.progress = tk.DoubleVar(value=0)
        self._configure_window()
        self._build(controls)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Control-o>', lambda _event: self.browse_source())
        self.root.bind('<Control-Return>', lambda _event: self.start_job())
        self.root.report_callback_exception = self.callback_error
        self.root.after(100, self.poll_events)
        self.root.after(150, self.load_source_preview)
        self.root.after(200, self.refresh_history)
        self.root.after(50, lambda: dark_titlebar(self.root))

    def _configure_window(self):
        self.root.title('DLSSNR · обработка изображения')
        self.root.configure(background=BACKGROUND)
        width = min(1320, self.root.winfo_screenwidth() - 80)
        height = min(940, self.root.winfo_screenheight() - 80)
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(980, 720)
        configure_theme(self.root)

    def _build(self, controls):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        header = ttk.Frame(self.root, padding=(24, 18, 24, 16))
        header.grid(row=0, column=0, sticky='ew')
        heading = ttk.Frame(header)
        heading.pack(side='left')
        ttk.Label(heading, text='DLSSNR · Portable' if NATIVE_MODE else 'DLSSNR / PyTorch', style='Muted.TLabel').pack(anchor='w', pady=(0, 3))
        ttk.Label(heading, text='Обработка изображения', style='Title.TLabel').pack(anchor='w')
        self.history_toggle = Toggle(header, text='История', variable=self.show_history,
                                     command=self.toggle_history, background=BACKGROUND)
        self.history_toggle.pack(side='right', padx=(16, 0))
        ttk.Label(header, text='Ctrl+O  Выбрать файл     Ctrl+Enter  Обработать', style='Muted.TLabel').pack(side='right')
        files_card = Card(self.root, padding=14)
        files_card.grid(row=1, column=0, sticky='ew', padx=24, pady=(0, 12))
        files = files_card.content
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text='Изображение', style='Card.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 12))
        entry = ttk.Entry(files, textvariable=self.source)
        entry.grid(row=0, column=1, sticky='ew', padx=(0, 10))
        entry.bind('<FocusOut>', lambda _event: self.load_source_preview())
        entry.bind('<Return>', lambda _event: self.load_source_preview(force=True))
        browse = Button(files, text='Выбрать файл', command=self.browse_source)
        browse.grid(row=0, column=2, sticky='ew')
        ttk.Label(files, text='Сохранить в', style='Card.TLabel').grid(row=1, column=0, sticky='w', padx=(0, 12), pady=(8, 0))
        output_entry = ttk.Entry(files, textvariable=self.output_base)
        output_entry.grid(row=1, column=1, sticky='ew', padx=(0, 10), pady=(8, 0))
        output_entry.bind('<FocusOut>', lambda _event: self.refresh_history())
        output_entry.bind('<Return>', lambda _event: self.refresh_history())
        choose = Button(files, text='Выбрать папку', command=self.browse_output)
        choose.grid(row=1, column=2, sticky='ew', pady=(8, 0))
        self.editable_widgets += [(entry, 'normal'), (output_entry, 'normal'), (browse, 'normal'), (choose, 'normal')]
        body = ttk.Frame(self.root)
        self.body = body
        body.grid(row=2, column=0, sticky='nsew', padx=24)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        settings_card = Card(body, width=326, padding=14)
        settings_card.grid(row=0, column=0, sticky='ns', padx=(0, 12))
        settings_card.pack_propagate(False)
        settings_outer = settings_card.content
        settings_outer.rowconfigure(0, weight=1)
        settings_outer.columnconfigure(0, weight=1)
        scroll = tk.Canvas(settings_outer, background=PANEL, highlightthickness=0, width=285, height=1)
        scroll.grid(row=0, column=0, sticky='nsew')
        bar = Scrollbar(settings_outer, command=scroll.yview)
        bar.grid(row=0, column=1, sticky='ns')
        scroll.configure(yscrollcommand=bar.set)
        self.settings_scroll = scroll
        card = ttk.Frame(scroll, style='Card.TFrame', padding=(0, 0, 8, 0))
        window = scroll.create_window((0, 0), window=card, anchor='nw')
        card.bind('<Configure>', lambda _event: scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>', lambda event: scroll.itemconfigure(window, width=event.width))
        self.root.bind('<MouseWheel>', lambda event: self._scroll_settings(event, scroll, card))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text='Параметры нового запуска', style='Heading.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 16))
        ttk.Label(card, text='Разрешение · длинная сторона', style='Card.TLabel').grid(row=1, column=0, sticky='w')
        resolution = ttk.Combobox(card, textvariable=self.resolution, values=list(RESOLUTIONS), state='readonly')
        resolution.grid(row=2, column=0, sticky='ew', pady=(5, 3))
        resolution.bind('<<ComboboxSelected>>', lambda _event: self.update_dimensions())
        self.custom_row = ttk.Frame(card, style='Card.TFrame')
        self.custom_row.grid(row=3, column=0, sticky='ew', pady=(3, 0))
        ttk.Label(self.custom_row, text='Пикселей:', style='Card.TLabel').pack(side='left')
        custom = ttk.Entry(self.custom_row, textvariable=self.custom_edge, width=9, justify='right')
        custom.pack(side='right')
        custom.bind('<FocusOut>', lambda _event: self.update_dimensions())
        ttk.Label(card, textvariable=self.dimensions, style='CardMuted.TLabel', wraplength=280).grid(row=4, column=0, sticky='w', pady=(5, 14))
        denoise_row = ttk.Frame(card, style='Card.TFrame')
        denoise_row.grid(row=5, column=0, sticky='ew', pady=(0, 16))
        denoise_row.columnconfigure(0, weight=1)
        self.denoise_toggle = Toggle(denoise_row, text='Денойзер', variable=self.denoise_enabled,
                                     command=self.update_denoise)
        self.denoise_toggle.grid(row=0, column=0, sticky='w', pady=(0, 6))
        self.denoise_backend_segments = Segments(denoise_row, values=['OptiX 3.0', 'Intel OIDN'],
                                                 variable=self.denoise_backend, width=268)
        self.denoise_backend_segments.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self.denoise_segments = Segments(denoise_row, values=['До DLSSNR', 'После DLSSNR'],
                                         variable=self.denoise_position, width=268)
        self.denoise_segments.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        self.denoise_strength = NumericControl(denoise_row, 'Сила денойза (0–1)', self.denoise_strength_value, maximum=1)
        self.denoise_strength.frame.grid(row=3, column=0, sticky='ew')
        self.editable_widgets.append((self.denoise_toggle, 'normal'))
        self.update_denoise()
        row = ttk.Frame(card, style='Card.TFrame')
        row.grid(row=6, column=0, sticky='ew', pady=(0, 12))
        ttk.Label(row, text='Стиль', style='Card.TLabel').pack(side='left')
        style_combo = Segments(row, values=['0', '1', '2'], variable=self.style, width=130)
        style_combo.pack(side='right')
        self.numeric = {}
        for index, (key, label) in enumerate([('intensity', 'Интенсивность'), ('local_tone', 'Local Tone'),
                                             ('local_structure', 'Local Structure')], 7):
            control = NumericControl(card, label, controls[key], maximum=1 if NATIVE_MODE and key == 'intensity' else 2)
            control.frame.grid(row=index, column=0, sticky='ew', pady=(0, 8))
            self.numeric[key] = control
        link = Toggle(card, text='Связать структуру кожи',
                      variable=self.skin_link, command=self.update_skin)
        link.grid(row=10, column=0, sticky='w', pady=(0, 4))
        skin = NumericControl(card, 'Skin Structure', max(0, controls['skin_structure']), maximum=0.99 if NATIVE_MODE else 2)
        skin.frame.grid(row=11, column=0, sticky='ew', pady=(0, 8))
        self.numeric['skin_structure'] = skin
        mask = Toggle(card, text='Автоматическая маска кожи', variable=self.auto_mask, command=self.update_skin)
        mask.grid(row=12, column=0, sticky='w', pady=(0, 8))
        row = ttk.Frame(card, style='Card.TFrame')
        row.grid(row=13, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(row, text='Seed', style='Card.TLabel').pack(side='left')
        seed = ttk.Entry(row, textvariable=self.seed, width=12, justify='right')
        seed.pack(side='right')
        if NATIVE_MODE:
            row.grid_remove()
        device = ttk.Combobox(card, textvariable=self.device, values=['GPU (CUDA)'] if NATIVE_MODE else ['GPU (CUDA)', 'CPU'], state='readonly')
        device.grid(row=14, column=0, sticky='ew', pady=(0, 10))
        if NATIVE_MODE:
            device.grid_remove()
            ttk.Label(card, text='NVIDIA RTX', style='CardMuted.TLabel').grid(row=14, column=0, sticky='w', pady=(0, 10))
        reset = Button(card, text='Стандартные параметры', command=self.reset_controls)
        self.reset_button = reset
        self.skin_toggle, self.mask_toggle = link, mask
        self.style_segments = style_combo
        reset.grid(row=15, column=0, sticky='ew', pady=(0, 10))
        self.editable_widgets += [(resolution, 'readonly'), (custom, 'normal'), (style_combo, 'normal'),
                                  (link, 'normal'), (mask, 'normal'), (seed, 'normal'),
                                  (device, 'readonly'), (reset, 'normal')]
        viewer_card = Card(body, padding=14)
        viewer_card.grid(row=0, column=1, sticky='nsew')
        viewer = viewer_card.content
        viewer.columnconfigure(0, weight=1)
        viewer.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(viewer, style='Card.TFrame')
        toolbar.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)
        preview_label = ttk.Label(toolbar, textvariable=self.result_info, style='CardMuted.TLabel')
        self.preview_label = preview_label
        preview_label.grid(row=0, column=0, sticky='w', padx=(0, 12))
        self.preview_modes = Segments(toolbar, values=['Вход', 'Сравнить', 'Результат'], variable=self.preview_mode,
                                      command=self.draw_preview, width=238)
        self.preview_modes.grid(row=0, column=1, sticky='e')
        view_tools = ttk.Frame(toolbar, style='Card.TFrame')
        self.view_tools = view_tools
        self.toolbar_narrow = None
        view_tools.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        self.fit_button = Button(view_tools, text='Вписать', command=self.reset_view)
        self.fit_button.pack(side='left')
        self.actual_size_button = Button(view_tools, text='100%', command=self.actual_size)
        self.actual_size_button.pack(side='left', padx=(6, 12))
        ttk.Label(view_tools, textvariable=self.zoom_info, style='CardMuted.TLabel').pack(side='left')
        self.navigation_hint = ttk.Label(view_tools, text='Колесо — зум · ЛКМ / СКМ — сдвиг',
                                         style='CardMuted.TLabel')
        self.navigation_hint.pack(side='right')
        toolbar.bind('<Configure>', self.layout_viewer_toolbar)
        self.canvas = tk.Canvas(viewer, background='#0d0e12', highlightthickness=1,
                                highlightbackground='#24262e', borderwidth=0, takefocus=1)
        self.canvas.grid(row=1, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', self.resize_preview)
        self.canvas.bind('<Motion>', self.hover_wipe)
        self.canvas.bind('<Leave>', self.leave_wipe)
        self.canvas.bind('<Button-1>', self.begin_preview_drag)
        self.canvas.bind('<B1-Motion>', self.move_preview_drag)
        self.canvas.bind('<ButtonRelease-1>', self.end_preview_drag)
        self.canvas.bind('<Button-2>', self.begin_pan_drag)
        self.canvas.bind('<B2-Motion>', self.move_pan_drag)
        self.canvas.bind('<ButtonRelease-2>', self.end_pan_drag)
        self.canvas.bind('<MouseWheel>', self.zoom_mouse)
        self.canvas.bind('<Double-Button-1>', lambda _event: self.reset_view())
        self.canvas.bind('<Left>', lambda _event: self.move_wipe_keyboard(-1))
        self.canvas.bind('<Right>', lambda _event: self.move_wipe_keyboard(1))
        self.canvas.bind('<Home>', lambda _event: self.move_wipe_keyboard(target=0))
        self.canvas.bind('<End>', lambda _event: self.move_wipe_keyboard(target=100))
        compare = ttk.Frame(viewer, style='Card.TFrame')
        compare.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        compare.columnconfigure(1, weight=1)
        ttk.Label(compare, text='Вход', style='CardMuted.TLabel').grid(row=0, column=0, padx=(0, 12))
        ttk.Label(compare, textvariable=self.compare_hint, style='CardMuted.TLabel',
                  anchor='center').grid(row=0, column=1, sticky='ew')
        ttk.Label(compare, text='Результат', style='CardMuted.TLabel').grid(row=0, column=2, padx=(12, 0))
        actions = ttk.Frame(viewer, style='Card.TFrame')
        actions.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        self.open_result_button = Button(actions, text='Открыть PNG', command=self.open_result, state='disabled')
        self.open_result_button.pack(side='left')
        Button(actions, text='Папка результатов', command=self.open_folder).pack(side='right')
        self.history_panel = HistoryPanel(body, self.history_scope, self.show_result_settings,
                                           self.open_history, self.change_history_scope, self.hide_history,
                                           self.refresh_history, self.save_history_preferences)
        self.history_panel.grid(row=0, column=2, sticky='ns', padx=(12, 0))
        if not self.show_history.get():
            self.history_panel.grid_remove()
        footer = ttk.Frame(self.root, padding=(24, 14, 24, 14))
        footer.grid(row=3, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=850)
        status_label.grid(row=0, column=0, sticky='w', padx=(0, 16))
        footer.bind('<Configure>', lambda event: status_label.configure(wraplength=max(280, event.width - 410)))
        ttk.Label(footer, textvariable=self.elapsed, style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(3, 4))
        self.run_button = Button(footer, text='Обработать изображение', primary=True, command=self.start_job)
        self.run_button.grid(row=0, column=2, rowspan=2, padx=(10, 0))
        self.cancel_button = Button(footer, text='Остановить', command=self.cancel_job, state='disabled')
        self.cancel_button.grid(row=0, column=1, rowspan=2)
        Progress(footer, variable=self.progress).grid(row=2, column=0, columnspan=3, sticky='ew', pady=(8, 6))
        Toggle(footer, text='Показать журнал', variable=self.show_log, command=self.toggle_log,
               background=BACKGROUND).grid(row=3, column=0, sticky='w')
        self.log_frame = ttk.Frame(self.root, padding=(22, 0, 22, 12))
        self.log_frame.grid(row=4, column=0, sticky='ew')
        self.log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(self.log_frame, height=7, background='#0d0e12', foreground=MUTED,
                           font=('Consolas', 9), relief='flat', state='disabled', wrap='word')
        self.log.grid(row=0, column=0, sticky='ew')
        log_scroll = Scrollbar(self.log_frame, command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky='ns')
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log_frame.grid_remove()
        self.update_skin()
        self.update_dimensions()

    @staticmethod
    def _scroll_settings(event, canvas, card):
        widget = event.widget
        while widget is not None:
            if widget is card or widget is canvas:
                canvas.yview_scroll(-int(event.delta / 120), 'units')
                break
            widget = getattr(widget, 'master', None)

    def browse_source(self):
        if self.process is not None:
            return
        path = filedialog.askopenfilename(parent=self.root, title='Выберите изображение',
                                         filetypes=[('Изображения', '*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff'),
                                                    ('Все файлы', '*.*')])
        if path:
            self.source.set(path)
            self.load_source_preview(force=True)

    def browse_output(self):
        path = filedialog.askdirectory(parent=self.root, title='Папка для новых результатов',
                                      initialdir=clean_path(self.output_base.get()) or str(ROOT))
        if path:
            self.output_base.set(path)
            self.remember_history_root()
            self.refresh_history()

    def layout_viewer_toolbar(self, event):
        narrow = event.width < 520
        self.preview_label.configure(wraplength=max(80, event.width - (8 if narrow else 265)))
        if narrow == self.toolbar_narrow:
            return
        self.toolbar_narrow = narrow
        if narrow:
            self.preview_label.grid_configure(columnspan=2)
            self.preview_modes.grid_configure(row=1, column=0, columnspan=2, sticky='w', pady=(5, 0))
            self.view_tools.grid_configure(row=2)
            self.navigation_hint.pack_forget()
        else:
            self.preview_label.grid_configure(columnspan=1)
            self.preview_modes.grid_configure(row=0, column=1, columnspan=1, sticky='e', pady=0)
            self.view_tools.grid_configure(row=1)
            self.navigation_hint.pack(side='right')

    def remember_history_root(self):
        value = clean_path(self.output_base.get())
        if value and Path(value).is_dir():
            keys = {source_key(path) for path in self.history_roots}
            if source_key(value) not in keys:
                self.history_roots.append(str(Path(value).expanduser().absolute()))

    def refresh_history(self):
        self.remember_history_root()
        self.history_records = scan_history(self.history_roots)
        self.render_history()

    def render_history(self, reset_scroll=False):
        records = (for_source(self.history_records, self.loaded_path)
                   if self.history_scope.get() == 'Текущий файл' else self.history_records)
        self.history_panel.set_records(records, reset_scroll=reset_scroll)

    def change_history_scope(self):
        self.render_history(reset_scroll=True)
        self.save_history_preferences()

    def toggle_history(self):
        self.history_panel.grid() if self.show_history.get() else self.history_panel.grid_remove()
        self.save_history_preferences()

    def hide_history(self):
        self.show_history.set(False)
        self.toggle_history()

    def history_preferences(self):
        return dict(show_history=self.show_history.get(), show_result_settings=self.show_result_settings.get(),
                    history_scope=self.history_scope.get(), history_roots=list(self.history_roots))

    def save_history_preferences(self):
        if self.persist:
            try:
                preferences = load_preferences()
                preferences.update(self.history_preferences())
                write_json(PREFERENCES, preferences)
            except OSError as error:
                self.append_log(f'Не удалось запомнить вид истории: {error}')

    def open_history(self, record):
        if self.process is not None:
            return
        if self.display_generation(record, compare=True):
            self.status.set(f'История · {record.source_name} · {record.date_label}')
            self.render_history()
            self.root.after_idle(self.history_panel.reveal_selected)

    def get_long_edge(self):
        value = RESOLUTIONS.get(self.resolution.get(), -1)
        if value == -1:
            value = int(self.custom_edge.get().strip())
            if not 1 <= value <= 16384:
                raise ValueError('Свой размер: от 1 до 16384 px по длинной стороне.')
        return value

    def get_config(self):
        values = {key: control.get() for key, control in self.numeric.items() if key != 'skin_structure'}
        values['skin_structure'] = -1.0 if self.skin_link.get() else self.numeric['skin_structure'].get()
        values.update(style=int(self.style.get()), seed=int(self.seed.get().strip()),
                      auto_mask=self.auto_mask.get())
        if self.skin_link.get():
            values['skin_structure'] = -1.0
        config = JobConfig(source=str(Path(clean_path(self.source.get())).expanduser().resolve()),
                           long_edge=self.get_long_edge(), device='cuda' if self.device.get() == 'GPU (CUDA)' else 'cpu',
                           controls=values, denoise_enabled=self.denoise_enabled.get(),
                           denoise_backend='oidn' if self.denoise_backend.get() == 'Intel OIDN' else 'optix',
                           denoise_position='after' if self.denoise_position.get() == 'После DLSSNR' else 'before',
                           denoise_strength=self.denoise_strength.get())
        config.validate()
        return config

    def reset_controls(self):
        for key, control in self.numeric.items():
            control.set(max(0, DEFAULT_CONTROLS[key]))
        self.style.set('0')
        self.seed.set('0')
        self.auto_mask.set(True)
        self.skin_link.set(True)
        self.denoise_enabled.set(DEFAULT_DENOISE['enabled'])
        self.denoise_backend.set('OptiX 3.0')
        self.denoise_position.set('До DLSSNR')
        self.denoise_strength.set(DEFAULT_DENOISE['strength'])
        self.update_denoise()
        self.update_skin()

    def update_denoise(self):
        enabled = self.denoise_enabled.get() and self.process is None
        self.denoise_backend_segments.configure(state='normal' if enabled else 'disabled')
        self.denoise_segments.configure(state='normal' if enabled else 'disabled')
        self.denoise_strength.enabled(enabled)

    def update_skin(self):
        self.numeric['skin_structure'].enabled(not self.skin_link.get() and self.auto_mask.get() and self.process is None)

    def update_dimensions(self):
        if self.resolution.get() == 'Свой размер':
            self.custom_row.grid()
        else:
            self.custom_row.grid_remove()
        if self.source_size:
            try:
                edge = self.get_long_edge()
                if not 0 <= edge <= 16384:
                    raise ValueError('Размер: 1–16384 px')
                width, height = target_size(self.source_size, edge)
                self.dimensions.set(f'{self.source_size[0]} × {self.source_size[1]} → {width} × {height}\n{width * height / 1e6:.1f} Мп · пропорции сохранены')
            except ValueError as error:
                self.dimensions.set(str(error))

    def load_source_preview(self, force=False):
        if self.process is not None:
            return
        path = clean_path(self.source.get())
        if not path or (path == self.loaded_path and not force):
            return
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
            self.source_size = image.size
            self.preview_original = image
            self.preview_result = None
            self.viewport = ImageViewport(image.size)
            self.loaded_path = path
            self.result_folder = None
            self.selected_generation = None
            self.history_panel.set_selected(None)
            self.open_result_button.configure(state='disabled')
            self.result_info.set(f'{Path(path).name} · {self.source_size[0]} × {self.source_size[1]}')
            self.update_dimensions()
            self.render_history(reset_scroll=True)
            self.fit_preview()
        except (OSError, ValueError) as error:
            self.source_size = None
            self.preview_original = self.preview_result = None
            self.viewport = None
            self.loaded_path = None
            self.result_folder = None
            self.selected_generation = None
            self.history_panel.set_selected(None)
            self.open_result_button.configure(state='disabled')
            self.render_history(reset_scroll=True)
            self.result_info.set('Изображение не загружено')
            self.dimensions.set('Выберите PNG, JPG, WebP, BMP или TIFF')
            self.fit_preview()
            if force:
                messagebox.showerror('Не удалось открыть изображение', str(error), parent=self.root)

    def resize_preview(self, _event=None):
        if self.resize_after:
            self.root.after_cancel(self.resize_after)
        self.resize_after = self.root.after(100, self.fit_preview)

    def fit_preview(self):
        self.resize_after = None
        self.wipe_dragging = False
        self.pan_dragging = False
        if self.viewport is not None:
            self.viewport.resize((self.canvas.winfo_width(), self.canvas.winfo_height()))
        self.render_preview()

    def render_preview(self):
        self.rendered_original = self.rendered_result = None
        if self.preview_original is not None and self.viewport is not None:
            self.rendered_original = self.viewport.render(self.preview_original)
            if self.preview_result is not None:
                self.rendered_result = self.viewport.render(self.preview_result)
            self.zoom_info.set(f'{self.viewport.zoom * 100:.1f}'.rstrip('0').rstrip('.') + '%')
        else:
            self.zoom_info.set('—')
        self.draw_preview()

    def reset_view(self):
        if self.viewport is not None:
            self.viewport.resize((self.canvas.winfo_width(), self.canvas.winfo_height()))
            self.viewport.fit()
            self.wipe_dragging = self.pan_dragging = False
            self.render_preview()
        return 'break'

    def set_zoom(self, zoom, anchor=None):
        if self.viewport is not None:
            size = (self.canvas.winfo_width(), self.canvas.winfo_height())
            self.viewport.resize(size)
            self.viewport.zoom_to(zoom, anchor or (size[0] / 2, size[1] / 2))
            self.wipe_dragging = self.pan_dragging = False
            self.render_preview()

    def actual_size(self):
        self.set_zoom(1.0)

    def zoom_mouse(self, event):
        if self.viewport is not None and event.delta:
            self.viewport.resize((self.canvas.winfo_width(), self.canvas.winfo_height()))
            steps = max(-10, min(10, event.delta / 120))
            self.set_zoom(self.viewport.zoom * 1.2 ** steps, (event.x, event.y))
        return 'break'

    def begin_preview_drag(self, event):
        if self.hit_wipe(event):
            return self.begin_wipe_drag(event)
        return self.begin_pan_drag(event)

    def move_preview_drag(self, event):
        if self.wipe_dragging:
            return self.move_wipe_drag(event)
        return self.move_pan_drag(event)

    def end_preview_drag(self, event):
        if self.wipe_dragging:
            self.end_wipe_drag(event)
        return self.end_pan_drag(event)

    def begin_pan_drag(self, event):
        if self.viewport is not None:
            self.wipe_dragging = False
            self.pan_dragging = True
            self.pan_last = (event.x, event.y)
            self.canvas.focus_set()
            self.canvas.configure(cursor='fleur')
            return 'break'

    def move_pan_drag(self, event):
        if self.pan_dragging and self.viewport is not None:
            dx, dy = event.x - self.pan_last[0], event.y - self.pan_last[1]
            self.pan_last = (event.x, event.y)
            if self.viewport.pan(dx, dy):
                self.render_preview()
            return 'break'

    def end_pan_drag(self, event):
        self.move_pan_drag(event)
        self.pan_dragging = False
        self.hover_wipe(event)

    def draw_preview(self):
        self.canvas.delete('all')
        self.preview_photos = []
        before = self.rendered_original
        after = self.rendered_result
        comparing = self.comparison_ready()
        self.compare_hint.set('Перетащите линию мышью' if comparing else
                              'Выберите «Сравнить»' if after is not None else
                              'Сравнение доступно после обработки')
        if not comparing:
            self.wipe_dragging = False
            self.canvas.configure(cursor='fleur' if self.pan_dragging else '')
        if before is None:
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    text='Выберите изображение\nдля предпросмотра', fill=MUTED,
                                    font=('Segoe UI', 15), justify='center')
            return
        mode = self.preview_mode.get()
        display = after if after is not None and mode != 'Вход' else before
        x, y, _width, _height = self.preview_box()
        photo = ImageTk.PhotoImage(display)
        self.preview_photos.append(photo)
        self.canvas.create_image(x, y, image=photo, anchor='nw')
        if comparing:
            self.draw_wipe()

    def comparison_ready(self):
        return (self.rendered_original is not None and self.rendered_result is not None
                and self.preview_mode.get() == 'Сравнить')

    def preview_box(self):
        return self.viewport.visible_box

    def draw_wipe(self):
        """Redraw only the moving overlay; keep the result image during dragging."""
        if not self.comparison_ready():
            return
        self.canvas.delete('comparison-overlay')
        self.preview_photos = self.preview_photos[:1]
        before = self.rendered_original
        x, y, width, height = self.preview_box()
        split = round(width * self.wipe.get() / 100)
        if split > 0:
            cropped = ImageTk.PhotoImage(before.crop((0, 0, split, height)))
            self.preview_photos.append(cropped)
            self.canvas.create_image(x, y, image=cropped, anchor='nw', tags='comparison-overlay')
        divider, middle = x + split, y + height / 2
        self.canvas.create_line(divider, y, divider, y + height, fill=ACCENT, width=1,
                                tags='comparison-overlay')
        self.canvas.create_oval(divider - 14, middle - 14, divider + 14, middle + 14,
                                fill=PANEL, outline=ACCENT, width=1, tags='comparison-overlay')
        for direction in (-1, 1):
            self.canvas.create_line(divider + direction * 3, middle - 4,
                                    divider + direction * 7, middle,
                                    divider + direction * 3, middle + 4,
                                    fill=TEXT, width=1, tags='comparison-overlay')

    def hit_wipe(self, event):
        if not self.comparison_ready():
            return False
        x, y, width, height = self.preview_box()
        divider = x + round(width * self.wipe.get() / 100)
        return (abs(event.x - divider) <= 15 and
                (y <= event.y <= y + height or abs(event.y - (y + height / 2)) <= 15))

    def hover_wipe(self, event):
        self.canvas.configure(cursor='fleur' if self.pan_dragging else
                              'sb_h_double_arrow' if self.wipe_dragging or self.hit_wipe(event) else '')

    def leave_wipe(self, _event):
        if not self.wipe_dragging and not self.pan_dragging:
            self.canvas.configure(cursor='')

    def begin_wipe_drag(self, event):
        if self.hit_wipe(event):
            x, _y, width, _height = self.preview_box()
            self.wipe_grab_offset = event.x - (x + round(width * self.wipe.get() / 100))
            self.wipe_dragging = True
            self.canvas.focus_set()
            self.canvas.configure(cursor='sb_h_double_arrow')
            return 'break'

    def move_wipe_drag(self, event):
        if self.wipe_dragging and self.comparison_ready():
            x, _y, width, _height = self.preview_box()
            value = (event.x - self.wipe_grab_offset - x) / width * 100
            self.wipe.set(min(100, max(0, value)))
            self.draw_wipe()
            return 'break'

    def end_wipe_drag(self, event):
        self.move_wipe_drag(event)
        self.wipe_dragging = False
        self.hover_wipe(event)

    def move_wipe_keyboard(self, delta=0, target=None):
        if self.comparison_ready():
            self.wipe.set(min(100, max(0, self.wipe.get() + delta if target is None else target)))
            self.draw_wipe()
            return 'break'

    def save_preferences(self, config):
        if not self.persist:
            return
        write_json(PREFERENCES, dict(source=config.source, long_edge=config.long_edge,
                                    device=config.device, controls=config.controls,
                                    denoise_enabled=config.denoise_enabled,
                                    denoise_backend=config.denoise_backend,
                                    denoise_position=config.denoise_position,
                                    denoise_strength=config.denoise_strength,
                                    output_base=clean_path(self.output_base.get()), **self.history_preferences()))

    def set_busy(self, busy):
        for widget, normal_state in self.editable_widgets:
            widget.configure(state='disabled' if busy else normal_state)
        for control in self.numeric.values():
            control.enabled(not busy)
        self.run_button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.history_panel.set_enabled(not busy)
        self.update_skin()
        self.denoise_backend_segments.configure(state='normal' if not busy and self.denoise_enabled.get() else 'disabled')
        self.denoise_segments.configure(state='normal' if not busy and self.denoise_enabled.get() else 'disabled')
        self.denoise_strength.enabled(not busy and self.denoise_enabled.get())

    def start_job(self):
        if self.process is not None:
            return
        try:
            config = self.get_config()
            if not clean_path(self.output_base.get()):
                raise ValueError('Выберите папку для сохранения результата.')
            with Image.open(config.source) as image:
                size = ImageOps.exif_transpose(image).size
            target_size(size, config.long_edge)
            python = Path(sys.executable).with_name('python.exe') if os.name == 'nt' else Path(sys.executable)
            if not python.is_file():
                raise FileNotFoundError('Не найден Python в текущем окружении.')
            if NATIVE_MODE:
                from native_backend import check_available
                check_available()
            elif not (ROOT / 'Model/weights.safetensors').is_file():
                raise FileNotFoundError('Не найдены веса Model/weights.safetensors.')
            if config.denoise_enabled and config.denoise_strength > 0:
                from denoise import check_available
                check_available(backend=config.denoise_backend)
            self.load_source_preview()
            self.current_folder = create_job(config, self.output_base.get())
            self.remember_history_root()
            self.completed = False
            self.cancel_requested = False
            self.started = time.perf_counter()
            self.progress.set(0)
            self.status.set('Запуск обработки…')
            self.log.configure(state='normal')
            self.log.delete('1.0', 'end')
            self.log.configure(state='disabled')
            environment = os.environ.copy()
            environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                               DLSSNR_COMPILE='0', DLSSNR_TRITON='0')
            self.process = subprocess.Popen([str(python), '-u', str(ROOT / 'App/engine.py'),
                                             str(self.current_folder / 'request.json')], cwd=str(ROOT),
                                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding='utf-8', errors='replace', bufsize=1,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.set_busy(True)
            try:
                self.save_preferences(config)
            except OSError as error:
                self.append_log(f'Не удалось запомнить настройки: {error}')
            threading.Thread(target=self.read_process, args=(self.process, self.current_folder), daemon=True).start()
        except (ValueError, OSError) as error:
            self.status.set('Проверьте файл и параметры.')
            messagebox.showerror('Не удалось запустить обработку', str(error), parent=self.root)

    def read_process(self, process, folder):
        try:
            with (folder / 'log.txt').open('w', encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict) or 'event' not in event:
                            raise ValueError('Not an event')
                    except ValueError:
                        event = {'event': 'log', 'message': line.rstrip()}
                    self.events.put(event)
            process.stdout.close()
            self.events.put({'event': 'ended', 'returncode': process.wait()})
        except Exception as error:
            self.events.put({'event': 'reader_error', 'message': str(error)})

    def poll_events(self):
        if self.closing:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['event']
            if kind == 'progress':
                if not self.cancel_requested:
                    self.progress.set(event['percent'])
                    self.status.set(event['message'])
                if 'gpu' in event:
                    self.append_log(f'Устройство: {event["gpu"]}')
            elif kind == 'complete':
                self.completed = True
                self.result_folder = Path(event['folder'])
                self.progress.set(100)
                seconds = event.get('processing_seconds', event['inference_seconds'])
                self.status.set(f'Готово · {event["size"][0]} × {event["size"][1]} · обработка {seconds:.1f} с')
                self.append_log(f'Результат: {self.result_folder / "output.png"}')
                self.show_result()
            elif kind == 'error':
                self.status.set('Обработка не завершилась. Подробности в журнале.')
                self.append_log(event.get('details', event['message']))
                if not self.cancel_requested:
                    messagebox.showerror('Ошибка обработки', event['message'], parent=self.root)
            elif kind == 'ended':
                self.finish_process(event['returncode'])
            elif kind == 'reader_error':
                self.append_log(f'Ошибка журнала: {event["message"]}')
                self.cancel_job()
                self.root.after(150, self._check_terminated)
            else:
                self.append_log(event.get('message', str(event)))
        if self.process is not None:
            self.elapsed.set(f'Прошло {time.perf_counter() - self.started:.0f} с')
        self.root.after(100, self.poll_events)

    def _check_terminated(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.root.after(150, self._check_terminated)
            else:
                self.finish_process(self.process.returncode)

    def mark_cancelled(self):
        if self.current_folder:
            path = self.current_folder / 'settings.json'
            try:
                report = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                if report.get('status') != 'passed':
                    report.update(status='cancelled')
                    write_json(path, report)
            except (OSError, ValueError) as error:
                self.append_log(f'Не удалось записать отмену: {error}')

    def finish_process(self, returncode):
        self.process = None
        # Recover a saved result even if termination happened just after saving
        # or the final stdout event could not be delivered.
        if not self.completed and self.current_folder:
            try:
                saved = json.loads((self.current_folder / 'settings.json').read_text(encoding='utf-8'))
                if saved.get('status') == 'passed' and (self.current_folder / 'output.png').is_file():
                    self.completed = True
                    self.result_folder = self.current_folder
                    self.progress.set(100)
                    size = saved['processed_size']
                    seconds = saved.get('processing_seconds', saved['inference_seconds'])
                    self.status.set(f'Готово · {size[0]} × {size[1]} · обработка {seconds:.1f} с')
                    self.show_result()
            except (OSError, ValueError, KeyError):
                pass
        if self.cancel_requested and not self.completed:
            self.mark_cancelled()
            self.status.set('Обработка остановлена. Можно изменить настройки и повторить запуск.')
            self.progress.set(0)
        elif not self.completed:
            self.status.set(f'Обработка завершилась с ошибкой (код {returncode}). Откройте журнал.')
            self.append_log(f'Задание: {self.current_folder}')
        self.elapsed.set(f'Общее время: {time.perf_counter() - self.started:.1f} с')
        self.set_busy(False)

    def cancel_job(self):
        if self.process is not None and self.process.poll() is None:
            self.cancel_requested = True
            self.cancel_button.configure(state='disabled')
            self.status.set('Остановка обработки…')
            self.process.terminate()

    def show_result(self):
        record = read_generation(self.result_folder)
        if record is not None:
            self.display_generation(record)
            self.refresh_history()
            self.root.after_idle(self.history_panel.reveal_selected)
        else:
            self.append_log('Не удалось открыть сохраненные параметры и PNG результата.')

    def display_generation(self, record, compare=False):
        try:
            images = []
            for filename in ('input.png', 'output.png'):
                with Image.open(record.folder / filename) as image:
                    images.append(image.convert('RGB'))
            if images[0].size != images[1].size:
                raise ValueError('Размеры входа и результата различаются.')
            keep_view = (self.viewport is not None and self.viewport.image_size == images[0].size
                         and source_key(self.loaded_path) == source_key(record.source))
            self.preview_original, self.preview_result = images
            if not keep_view:
                self.viewport = ImageViewport(images[0].size)
                self.wipe.set(50)
            self.result_folder = record.folder
            self.selected_generation = record
            self.source.set(record.source)
            self.loaded_path = clean_path(record.source)
            size = record.settings.get('source_size')
            self.source_size = tuple(size) if isinstance(size, list) and len(size) == 2 and all(isinstance(n, int) and n > 0 for n in size) else images[0].size
            self.result_info.set(f'{record.source_name} · {record.size_label}\n{record.date_label}')
            self.history_panel.set_selected(record)
            self.open_result_button.configure(state='normal')
            if compare:
                self.preview_mode.set('Сравнить')
            self.update_dimensions()
            self.fit_preview()
            return True
        except (OSError, ValueError) as error:
            self.append_log(f'Не удалось загрузить результат из истории: {error}')
            messagebox.showerror('Не удалось открыть результат', str(error), parent=self.root)
            return False

    def open_result(self):
        if self.result_folder:
            self.open_path(self.result_folder / 'output.png')

    def open_folder(self):
        path = self.result_folder or self.current_folder or Path(clean_path(self.output_base.get()))
        if path.exists():
            self.open_path(path)
        else:
            messagebox.showinfo('Папка результатов', 'Папка появится после первого запуска обработки.', parent=self.root)

    def open_path(self, path):
        try:
            os.startfile(str(path))
        except OSError as error:
            messagebox.showerror('Не удалось открыть файл', str(error), parent=self.root)

    def append_log(self, message):
        self.log.configure(state='normal')
        self.log.insert('end', message + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def toggle_log(self):
        self.log_frame.grid() if self.show_log.get() else self.log_frame.grid_remove()

    def callback_error(self, exception, value, tb):
        details = ''.join(traceback.format_exception(exception, value, tb))
        self.append_log(details)
        messagebox.showerror('Ошибка приложения', str(value), parent=self.root)

    def close(self):
        self.closing = True
        try:
            self.save_preferences(self.get_config())
        except (ValueError, OSError):
            self.save_history_preferences()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
            if not self.completed:
                self.mark_cancelled()
        self.root.destroy()


def enable_dpi_awareness():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        details = traceback.format_exc()
        (ROOT / 'App/startup-error.txt').write_text(details, encoding='utf-8')
        messagebox.showerror('DLSSNR: ошибка запуска', details)
