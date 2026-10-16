"""Read completed application jobs without loading the model or full images."""
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path


def source_key(value):
    """Windows file identity: full path, independent of case and slash spelling."""
    if not value:
        return ''
    value = str(value).strip().strip('"').strip("'")
    return os.path.normcase(os.path.abspath(os.path.expanduser(value)))


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass
class Generation:
    folder: Path
    source: str
    settings: dict
    generated_at: datetime
    date_estimated: bool = False

    @property
    def key(self):
        return source_key(self.folder)

    @property
    def source_name(self):
        return Path(self.source).name if self.source else self.folder.name

    @property
    def date_label(self):
        label = self.generated_at.astimezone().strftime('%d.%m.%Y · %H:%M:%S')
        return label + (' (по файлу)' if self.date_estimated else '')

    @property
    def size_label(self):
        size = self.settings.get('processed_size')
        if isinstance(size, (list, tuple)) and len(size) == 2:
            return f'{size[0]} × {size[1]}'
        return 'Размер не указан'

    @property
    def controls(self):
        controls = self.settings.get('controls', {})
        return controls if isinstance(controls, dict) else {}

    def details(self):
        def number(key):
            value = self.controls.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
            return f'{value:.12g}' if isinstance(value, float) else '—'

        skin = self.controls.get('skin_structure')
        skin_text = 'Связана с Local Structure' if isinstance(skin, (int, float)) and skin < 0 else number('skin_structure')
        mask = self.controls.get('auto_mask')
        mask_text = 'Вкл.' if mask is True else 'Выкл.' if mask is False else '—'
        device = self.settings.get('gpu') or ('CPU' if self.settings.get('device') == 'cpu' else 'GPU (CUDA)')
        denoise = self.settings.get('denoise', {})
        denoise = denoise if isinstance(denoise, dict) else {}
        denoise_text = 'Выкл.'
        denoise_name = 'Intel OIDN' if denoise.get('backend') == 'oidn' else 'OptiX 3.0'
        if denoise.get('enabled'):
            position = 'после' if denoise.get('position') == 'after' else 'до'
            strength = denoise.get('strength', 0)
            strength_text = f'{strength:.12g}' if isinstance(strength, (int, float)) else '—'
            denoise_text = f'{position} DLSSNR · сила {strength_text}'
        return '\n'.join([
            f'Разрешение: {self.size_label}',
            f'{denoise_name}: {denoise_text}',
            f'Стиль: {number("style")}  ·  Интенсивность: {number("intensity")}',
            f'Local Tone: {number("local_tone")}',
            f'Local Structure: {number("local_structure")}',
            f'Skin Structure: {skin_text}',
            f'Автомаска кожи: {mask_text}',
            *([] if self.settings.get('engine') == 'native' else [f'Seed: {number("seed")}']),
            f'Устройство: {device}',
        ])


def read_generation(folder):
    folder = Path(folder)
    settings_path = folder / 'settings.json'
    settings = _read_json(settings_path)
    if settings.get('status') != 'passed':
        return None
    if not all((folder / name).is_file() for name in ('input.png', 'output.png')):
        return None
    request = _read_json(folder / 'request.json').get('job', {})
    if not isinstance(request, dict):
        request = {}
    source = settings.get('source') or request.get('source') or ''
    if not isinstance(source, str):
        source = ''
    if not isinstance(settings.get('controls'), dict) and isinstance(request.get('controls'), dict):
        settings['controls'] = request['controls']
    for name in ('finished_utc', 'started_utc'):
        try:
            value = datetime.fromisoformat(settings.get(name, '').replace('Z', '+00:00'))
            if value.tzinfo is None:
                value = value.astimezone()
            return Generation(folder, source, settings, value)
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        date = datetime.fromtimestamp(settings_path.stat().st_mtime).astimezone()
        return Generation(folder, source, settings, date, date_estimated=True)
    except OSError:
        return None


def scan_history(roots):
    """Only direct job folders, never recursive searches through other projects."""
    records, visited = {}, set()
    for root in roots:
        root = Path(root)
        key = source_key(root)
        if key in visited:
            continue
        visited.add(key)
        try:
            paths = list(root.glob('*/settings.json'))
        except OSError:
            continue
        for path in paths:
            record = read_generation(path.parent)
            if record is not None:
                records[record.key] = record
    return sorted(records.values(), key=lambda item: (item.generated_at.timestamp(), item.key), reverse=True)


def for_source(records, source):
    key = source_key(source)
    return [record for record in records if key and source_key(record.source) == key]
