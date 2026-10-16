"""Application settings and job folders, without importing PyTorch."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'Results'
PREFERENCES = ROOT / 'App/preferences.json'
PORTABLE = ROOT / 'portable.json'
NATIVE_MODE = PORTABLE.is_file() and json.loads(PORTABLE.read_text(encoding='utf-8')).get('engine') == 'native'
DEFAULT_CONTROLS = dict(intensity=1.0, style=0, local_tone=1.0,
                        local_structure=1.0, skin_structure=-1.0,
                        auto_mask=True, seed=0)
DEFAULT_DENOISE = dict(enabled=False, backend='optix', position='before', strength=0.5)
RESOLUTIONS = {'Исходный размер': 0, '2K — 2048 px': 2048, '4K — 4096 px': 4096,
               '5K — 5120 px': 5120, '6K — 6144 px': 6144,
               '8K — 8192 px': 8192, 'Свой размер': -1}


@dataclass
class JobConfig:
    source: str
    long_edge: int = 6144
    device: str = 'cuda'
    controls: dict = field(default_factory=lambda: DEFAULT_CONTROLS.copy())
    denoise_enabled: bool = DEFAULT_DENOISE['enabled']
    denoise_backend: str = DEFAULT_DENOISE['backend']
    denoise_position: str = DEFAULT_DENOISE['position']
    denoise_strength: float = DEFAULT_DENOISE['strength']

    def validate(self):
        if not Path(self.source).is_file():
            raise ValueError('Выберите существующее изображение.')
        if type(self.long_edge) is not int or not 0 <= self.long_edge <= 16384:
            raise ValueError('Длинная сторона должна быть от 1 до 16384 px или «Исходный размер».')
        if self.device not in ('cuda', 'cpu'):
            raise ValueError('Выберите GPU (CUDA) или CPU.')
        if NATIVE_MODE and self.device != 'cuda':
            raise ValueError('Переносимая сборка требует видеокарту NVIDIA RTX.')
        if NATIVE_MODE and (self.controls.get('seed') != 0 or self.controls.get('intensity', 0) > 1):
            raise ValueError('Переносимая сборка: интенсивность 0–1, Seed 0.')
        if NATIVE_MODE and self.controls.get('skin_structure', -1) != -1 and not 0 <= self.controls.get('skin_structure', -1) <= 0.99:
            raise ValueError('Skin Structure: связь со структурой либо значение 0–0.99.')
        if type(self.denoise_enabled) is not bool:
            raise ValueError('Денойзер должен быть включён или выключен.')
        if self.denoise_backend not in ('optix', 'oidn'):
            raise ValueError('Выберите OptiX или Intel OIDN.')
        if self.denoise_position not in ('before', 'after'):
            raise ValueError('Выберите денойз до или после DLSSNR.')
        strength = self.denoise_strength
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError('Сила денойзера: от 0 до 1.')
        if set(self.controls) != set(DEFAULT_CONTROLS):
            raise ValueError('Неполный или неизвестный набор настроек DLSSNR.')
        for key, minimum in [('intensity', 0), ('local_tone', 0),
                             ('local_structure', 0), ('skin_structure', -1)]:
            value = self.controls[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= 2:
                raise ValueError(f'{key}: допустимо значение от {minimum} до 2.')
        if type(self.controls['style']) is not int or self.controls['style'] not in (0, 1, 2):
            raise ValueError('Стиль должен быть 0, 1 или 2.')
        if type(self.controls['auto_mask']) is not bool:
            raise ValueError('Автоматическая маска должна быть включена или выключена.')
        if type(self.controls['seed']) is not int or not 0 <= self.controls['seed'] <= 0xFFFFFFFF:
            raise ValueError('Seed должен быть целым числом от 0 до 4294967295.')


def target_size(size, long_edge):
    if type(long_edge) is not int or not 0 <= long_edge <= 16384:
        raise ValueError('Длинная сторона должна быть от 1 до 16384 px или «Исходный размер».')
    width, height = size
    if width < 1 or height < 1:
        raise ValueError('Изображение имеет недопустимый размер.')
    if long_edge:
        ratio = long_edge / max(width, height)
        width, height = max(1, round(width * ratio)), max(1, round(height * ratio))
    if not (1 <= width <= 16384 and 1 <= height <= 16384):
        raise ValueError('Модель поддерживает до 16384 px по каждой стороне. Выберите меньший размер.')
    return width, height


def clean_path(value):
    return value.strip().strip('"').strip("'")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def create_job(config, output_base):
    config.validate()
    base = Path(clean_path(str(output_base))).expanduser().resolve()
    if base.is_file():
        raise ValueError('Место сохранения должно быть папкой.')
    stem = re.sub(r'[^\w .-]', '_', Path(config.source).stem)[:60].strip(' .') or 'image'
    folder = base / f'{stem}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}'
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / 'request.json', {'schema': 1, 'job': asdict(config)})
    return folder


def load_preferences():
    try:
        value = json.loads(PREFERENCES.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
