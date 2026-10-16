"""Portable OptiX/OIDN denoisers, RGB PNG and common strength blending."""
import hashlib
import math
from pathlib import Path
import subprocess
import time

import numpy as np
from PIL import Image

from config import ROOT

EXECUTABLE = ROOT / 'Reference/MaxNeuralStudio/optix-3.0/Denoiser.exe'
EXECUTABLE_SHA256 = '42dd0a099a3029f3271aa42a782aee06bbfd03bd2f466a2ecfa76c8666616058'
_verified = None


def check_available(verify=False, backend='optix'):
    global _verified
    if backend == 'oidn':
        from oidn_adapter import check_available as check_oidn
        return check_oidn(verify=verify)
    if backend != 'optix':
        raise ValueError('Выберите OptiX или Intel OIDN.')
    if not EXECUTABLE.is_file():
        raise FileNotFoundError('Не найден OptiX 3.0: Reference/MaxNeuralStudio/optix-3.0/Denoiser.exe.')
    if verify:
        stamp = (EXECUTABLE.stat().st_size, EXECUTABLE.stat().st_mtime_ns)
        if stamp != _verified:
            with EXECUTABLE.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != EXECUTABLE_SHA256:
                raise ValueError('Контрольная сумма OptiX 3.0 не совпала. Проверьте файл Denoiser.exe.')
            _verified = stamp


def validate_strength(strength):
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Сила денойзера: от 0 до 1.')


def blend(original, filtered, strength):
    validate_strength(strength)
    if original.shape != filtered.shape or original.dtype != np.uint8 or filtered.dtype != np.uint8:
        raise ValueError('Денойзер: вход и результат должны быть RGB8 одного размера.')
    if strength == 0:
        return original.copy()
    if strength == 1:
        return filtered.copy()
    return np.rint(original.astype(np.float32) * (1 - strength)
                   + filtered.astype(np.float32) * strength).clip(0, 255).astype(np.uint8)


def denoise(source, destination, strength, backend='optix', device='cuda'):
    validate_strength(strength)
    if backend not in ('optix', 'oidn'):
        raise ValueError('Выберите OptiX или Intel OIDN.')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    with Image.open(source) as image:
        original = np.asarray(image.convert('RGB')).copy()
    receipt = dict(backend=backend,
                   engine='Intel Open Image Denoise' if backend == 'oidn' else 'NvidiaAIDenoiser',
                   release='2.5.1' if backend == 'oidn' else '3.0',
                   guides='color-only', model='LDR', strength=strength,
                   input=source.name, output=destination.name, size=list(original.shape[1::-1]),
                   blend=f'round((1-strength)*input + strength*{backend.upper()})',
                   tone_correction=False)
    if backend == 'optix':
        receipt['optix_sdk'] = '9.1.0'
    # Strength zero is an exact bypass; it does not require the executable/GPU.
    if strength == 0:
        Image.fromarray(blend(original, original, strength)).save(destination)
        return receipt | dict(applied=False, seconds=0.0)
    if backend == 'oidn':
        from oidn_adapter import run_oidn
        filtered, details = run_oidn(original, destination, device)
        Image.fromarray(blend(original, filtered, strength)).save(destination)
        return receipt | details | dict(applied=True)
    check_available(verify=True)
    raw = destination.with_name(destination.stem + '.optix.png')
    log = destination.with_name(destination.stem + '.optix.log')
    started = time.perf_counter()
    with log.open('w', encoding='utf-8') as stream:
        completed = subprocess.run([str(EXECUTABLE), '-i', str(source), '-o', str(raw), '-hdr', '0'],
                                   cwd=str(EXECUTABLE.parent), stdout=stream, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode or not raw.is_file():
        tail = log.read_text(encoding='utf-8', errors='replace')[-1800:]
        raise RuntimeError(f'OptiX 3.0 не завершил денойз (код {completed.returncode}). '
                           f'Проверьте NVIDIA GPU/драйвер. Журнал: {log.name}\n{tail}')
    with Image.open(raw) as image:
        filtered = np.asarray(image.convert('RGB')).copy()
    output = blend(original, filtered, strength)
    Image.fromarray(output).save(destination)
    return receipt | dict(applied=True, seconds=time.perf_counter() - started,
                          raw_output=raw.name, log=log.name, executable_sha256=EXECUTABLE_SHA256)
