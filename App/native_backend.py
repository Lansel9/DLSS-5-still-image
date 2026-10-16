"""Pinned native NR helper. No PyTorch, no fallback and no desktop capture."""
from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import struct
import subprocess
import time

import numpy as np
from PIL import Image
from config import ROOT, write_json

PACKAGE = ROOT / 'Runtime/Native'
RUNTIME = PACKAGE / 'neural-runtime'


def check_available(verify=False):
    if not (RUNTIME / 'NeuralWorker.exe').is_file():
        raise FileNotFoundError('Не найден нативный движок. Распакуйте архив приложения полностью.')
    if verify:
        manifest = json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['sha256'].items():
            path = (PACKAGE / name).resolve()
            if not path.is_relative_to(PACKAGE.resolve()):
                raise ValueError('Неверный путь в манифесте движка.')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError(f'Поврежден файл движка: {path.name}. Распакуйте архив заново.')


@contextmanager
def runtime_lock():
    # A second application instance must not rewrite INI while a helper runs.
    with (PACKAGE / 'session.lock').open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Движок занят другим окном приложения. Дождитесь завершения обработки.')
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def decode_metadata(path):
    receipts = []
    with path.open('rb') as stream:
        while header := stream.read(12):
            if len(header) != 12:
                raise ValueError('Оборван заголовок ответа движка.')
            magic, version, kind, length = struct.unpack('<IHHI', header)
            if magic != 0x3152574E or version != 6 or length > 65536:
                raise ValueError('Неверный протокол движка.')
            payload = stream.read(length)
            if len(payload) != length:
                raise ValueError('Оборван ответ движка.')
            if kind == 2:
                if length < 140:
                    raise ValueError('Оборвано подтверждение обработки.')
                receipt = dict(ok=payload[0], cancelled=payload[1], armed=payload[3],
                               native_nr=payload[4], contract=payload[5], nr_created=payload[6],
                               nr_evaluated=payload[7], later_failure=payload[8], failure=payload[9],
                               sr_only=payload[10], frames=struct.unpack_from('<Q', payload, 16)[0],
                               dlss_evaluations=struct.unpack_from('<Q', payload, 32)[0],
                               verified_nr_frames=struct.unpack_from('<Q', payload, 40)[0],
                               job_id=struct.unpack_from('<Q', payload, 56)[0])
                for size in (152, 140):
                    if len(payload) >= size:
                        count = struct.unpack_from('<I', payload, size - 4)[0]
                        if count <= 4096 and count % 2 == 0 and size + count == len(payload):
                            receipt['detail'] = payload[size:].decode('utf-16-le')
                            break
                receipts.append(receipt)
    return receipts


def verified_receipt(receipt):
    return (all(receipt.get(key) == 1 for key in
                ('ok', 'armed', 'native_nr', 'contract', 'nr_created', 'nr_evaluated',
                 'frames', 'verified_nr_frames', 'job_id'))
            and receipt.get('dlss_evaluations', 0) > 0
            and all(receipt.get(key) == 0 for key in
                    ('cancelled', 'later_failure', 'failure', 'sr_only')))


def make_ini(controls):
    options = dict(ConfigVersion=6, EnableHooks=2, NeuralUplift=1, NRFollowInputRes=0,
                   NRResolutionScale=1, NRIntensity=controls['intensity'],
                   NRLocalTone=controls['local_tone'], NRLocalStructure=controls['local_structure'],
                   NRSkinStructure=controls['skin_structure'], NRColorStrength=1, NRPreset=0,
                   NRStyle=controls['style'], NRAutoMask=int(controls['auto_mask']), NRPasses=1,
                   NRChainedHistory=1, NRNormGovernor=1)
    return ('[ADDON]\nAddonPath=.\nDisabledAddons=\n\n'
            '[GENERAL]\nPresetPath=.\\ReShadePreset.ini\nPerformanceMode=0\n\n'
            '[OVERLAY]\nTutorialProgress=4\n\n[RenoDX.DLSS5]\n'
            + ''.join(f'{key}={value}\n' for key, value in options.items()))


def infer(frame, controls, folder, emit):
    from process_tree import protect_child_processes
    protect_child_processes()
    check_available(verify=True)
    width, height = frame.shape[1], frame.shape[0]
    source = folder / 'native-input.png'
    destination = folder / 'native-output.png'
    stage = folder / 'native-output.mkv'
    Image.fromarray(frame).save(source)
    started = time.perf_counter()
    with runtime_lock():
        ini = make_ini(controls)
        (RUNTIME / 'ReShade.ini').write_text(ini, encoding='utf-8')
        (folder / 'native-settings.ini').write_text(ini, encoding='utf-8')
        emit('progress', percent=30, message='Обработка DLSSNR…')
        receipt = None
        for attempt in range(2):
            metadata_path = folder / f'native-metadata-{attempt}.bin'
            log_path = folder / f'native-worker-{attempt}.log'
            with metadata_path.open('wb') as metadata, log_path.open('wb') as log:
                handle = msvcrt.get_osfhandle(metadata.fileno())
                os.set_handle_inheritable(handle, True)
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
                startup.lpAttributeList = {'handle_list': [handle]}
                command = [str(RUNTIME / 'NeuralWorker.exe'), '--neural-worker',
                           '--metadata-handle', str(handle), '--source', str(source), '--staging', str(stage),
                           '--width', str(width), '--height', str(height), '--fps', '1',
                           '--duration-100ns', '10000000', '--job-id', '1',
                           '--range-start-100ns', '0', '--range-end-100ns', '0',
                           '--preroll-frames', '30', '--frame-retry-limit', '2', '--guides', 'mv=1,depth=1',
                           '--cache-quality', 'lossless', '--require-neural', '1']
                if attempt:
                    command.append('--configuration-restarted')
                child = subprocess.Popen(command, cwd=RUNTIME, startupinfo=startup, close_fds=True,
                                         stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
                                         env=os.environ | {'PATH': str(PACKAGE / 'CRT') + os.pathsep + os.environ.get('PATH', '')})
                try:
                    code = child.wait(timeout=600)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
                    raise RuntimeError('Движок не ответил за 10 минут. Попробуйте меньшее разрешение.')
            if code == 75 and attempt == 0:
                continue
            receipts = decode_metadata(metadata_path)
            if code or len(receipts) != 1 or not verified_receipt(receipts[0]):
                detail = receipts[-1].get('detail', '') if receipts else ''
                raise RuntimeError(f'DLSSNR не подтвердил обработку (код {code}). '
                                   f'Требуются NVIDIA RTX и совместимый драйвер. {detail} '
                                   f'Подробности: {log_path.name}')
            receipt = receipts[0]
            break
        if receipt is None:
            raise RuntimeError('Не удалось подготовить конфигурацию движка.')
        write_json(folder / 'native-receipt.json', receipt)
        for name in ('NeuralWorker.log', 'ReShade.log'):
            path = RUNTIME / name
            if path.is_file():
                (folder / name).write_bytes(path.read_bytes())
    with (folder / 'native-extract.log').open('w', encoding='utf-8') as log:
        completed = subprocess.run([str(PACKAGE / 'ffmpeg.exe'), '-y', '-hide_banner', '-loglevel', 'error',
                                    '-i', str(stage), '-frames:v', '1', '-pix_fmt', 'rgb24', str(destination)],
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                                   timeout=120)
    if completed.returncode or not destination.is_file():
        raise RuntimeError('Не удалось прочитать результат DLSSNR. См. native-extract.log.')
    with Image.open(destination) as image:
        if image.size != (width, height):
            raise ValueError('Неверный размер результата DLSSNR.')
        result = np.asarray(image.convert('RGB')).copy()
    # These are our own intermediates; the input, final PNG, INI and receipts survive.
    stage.unlink(missing_ok=True)
    source.unlink(missing_ok=True)
    destination.unlink(missing_ok=True)
    return result, dict(engine='native', runtime='310.8.SF-v2 / helper 0.26.2',
                        gpu='NVIDIA RTX', inference_seconds=time.perf_counter() - started,
                        native_receipt=receipt, seed_supported=False, passes=1,
                        guides='estimated mv/depth', real_scene_depth=False)
