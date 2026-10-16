"""Official portable OIDN CLI, float PFM interchange and explicit LDR sRGB."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
from PIL import Image

from config import ROOT

VERSION = '2.5.1'
PACKAGE = ROOT / 'Reference/OIDN'
EXECUTABLE = PACKAGE / f'oidn-{VERSION}.x64.windows/bin/oidnDenoise.exe'
MANIFEST_SHA256 = 'c8f1652a252c3eae547df49b35d3b1092746dda1437372880e8a923002484a15'
_verified = None


def check_available(verify=False):
    global _verified
    if not EXECUTABLE.is_file():
        raise FileNotFoundError('Не найден Intel OIDN 2.5.1 в Reference/OIDN. Восстановите portable-пакет проекта.')
    if not verify:
        return
    manifest_path = PACKAGE / 'manifest.json'
    data = manifest_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Контрольная сумма манифеста OIDN не совпала.')
    manifest = json.loads(data)
    paths = []
    for name, digest in manifest['files_sha256'].items():
        path = (PACKAGE / name).resolve()
        if not path.is_relative_to(EXECUTABLE.parent.resolve()):
            raise ValueError('Путь OIDN выходит за папку бинарных файлов.')
        paths.append((path, digest))
    stamps = [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path, _ in paths]
    if stamps != _verified:
        for path, digest in paths:
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    raise ValueError(f'Контрольная сумма OIDN не совпала: {path.name}.')
        _verified = stamps


def write_pfm(path, image):
    values = np.asarray(image, dtype=np.float32)
    if values.ndim != 3 or values.shape[2] != 3 or min(values.shape[:2]) < 1 or not np.isfinite(values).all():
        raise ValueError('PFM требует конечное RGB float-изображение.')
    with Path(path).open('wb') as stream:
        stream.write(f'PF\n{values.shape[1]} {values.shape[0]}\n-1.0\n'.encode('ascii'))
        stream.write(np.flipud(values).astype('<f4').tobytes())


def read_pfm(path, expected_size):
    with Path(path).open('rb') as stream:
        if stream.readline().strip() != b'PF':
            raise ValueError('OIDN вернул не RGB PFM.')
        dimensions = stream.readline().split()
        if len(dimensions) != 2:
            raise ValueError('Некорректный размер PFM.')
        width, height = map(int, dimensions)
        if (width, height) != tuple(expected_size):
            raise ValueError('Размер результата OIDN отличается от входа.')
        scale = float(stream.readline())
        if not math.isfinite(scale) or scale == 0:
            raise ValueError('Некорректный scale PFM.')
        expected_bytes = width * height * 3 * 4
        data = stream.read(expected_bytes + 1)
        if len(data) != expected_bytes:
            raise ValueError('Некорректная длина данных PFM.')
    values = np.frombuffer(data, dtype='<f4' if scale < 0 else '>f4').reshape(height, width, 3)
    values = np.flipud(values).astype(np.float32) * abs(scale)
    if not np.isfinite(values).all():
        raise ValueError('OIDN вернул нечисловые значения пикселей.')
    return values


def run_oidn(original, destination, device):
    if device not in ('cuda', 'cpu'):
        raise ValueError('OIDN поддерживает выбор CUDA или CPU в этом приложении.')
    check_available(verify=True)
    destination = Path(destination)
    source_pfm = destination.with_name(destination.stem + '.oidn-input.pfm')
    output_pfm = destination.with_name(destination.stem + '.oidn.pfm')
    raw = destination.with_name(destination.stem + '.oidn.png')
    log = destination.with_name(destination.stem + '.oidn.log')
    started = time.perf_counter()
    write_pfm(source_pfm, original.astype(np.float32) / 255)
    command = [str(EXECUTABLE), '--ldr', str(source_pfm), '--srgb', '-o', str(output_pfm),
               '-d', device, '-f', 'RT', '-q', 'high', '--threads', '8', '--maxmem', '1024', '-v', '1']
    with log.open('w', encoding='utf-8') as stream:
        completed = subprocess.run(command, cwd=str(EXECUTABLE.parent), stdout=stream,
                                   stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode or not output_pfm.is_file():
        tail = log.read_text(encoding='utf-8', errors='replace')[-1800:]
        raise RuntimeError(f'Intel OIDN не завершил денойз (код {completed.returncode}). '
                           f'Устройство: {device}. Журнал: {log.name}\n{tail}')
    values = read_pfm(output_pfm, original.shape[1::-1])
    filtered = np.rint(np.clip(values, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(filtered).save(raw)
    return filtered, dict(raw_output=raw.name, log=log.name, input_pfm=source_pfm.name,
                          output_pfm=output_pfm.name, device=device, srgb=True,
                          quality='high', filter='RT', max_memory_mb=1024,
                          seconds=time.perf_counter() - started, manifest_sha256=MANIFEST_SHA256)
