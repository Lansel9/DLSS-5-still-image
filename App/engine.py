"""One isolated DLSSNR job. JSON lines on stdout keep the GUI responsive."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import sys
import time
import traceback

from config import JobConfig, NATIVE_MODE, ROOT, target_size, write_json


def emit(kind, **values):
    print(json.dumps(dict(event=kind, **values), ensure_ascii=False), flush=True)


def process(request_path):
    folder = request_path.resolve().parent
    report = {'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat()}
    net = frame = result = None
    hooks = []
    torch = None
    try:
        payload = json.loads(request_path.read_text(encoding='utf-8'))
        if payload.get('schema') != 1:
            raise ValueError('Неподдерживаемый формат задания.')
        config = JobConfig(**payload['job'])
        config.validate()
        report.update(source=config.source, device=config.device, controls=config.controls,
                      long_edge=config.long_edge, threads=8, compile=False, triton=False,
                      denoise=dict(enabled=config.denoise_enabled, backend=config.denoise_backend,
                                   position=config.denoise_position,
                                   strength=config.denoise_strength, applied=False))
        active_denoise = config.denoise_enabled and config.denoise_strength > 0
        denoise_name = 'Intel OIDN' if config.denoise_backend == 'oidn' else 'OptiX 3.0'
        denoise_stage = 'OIDN' if config.denoise_backend == 'oidn' else 'OptiX'
        if active_denoise or NATIVE_MODE:
            from process_tree import protect_child_processes
            protect_child_processes()
        write_json(folder / 'settings.json', report)
        emit('progress', percent=2, message='Загрузка изображения…')
        from PIL import Image, ImageDraw, ImageFont, ImageOps
        import numpy as np
        with Image.open(config.source) as source:
            original = ImageOps.exif_transpose(source).convert('RGB')
        report['source_size'] = list(original.size)
        width, height = target_size(original.size, config.long_edge)
        image = original if original.size == (width, height) else original.resize((width, height), Image.Resampling.LANCZOS)
        # The comparison always uses the untouched source at the output size.
        image.save(folder / 'input.png')
        report.update(processed_size=[width, height], input='input.png',
                      resize='Lanczos, preserve aspect ratio, EXIF orientation applied',
                      pipeline=['resize', 'DLSSNR'])
        model_image = image
        if active_denoise and config.denoise_position == 'before':
            from denoise import denoise
            emit('progress', percent=4, message=f'{denoise_name} · денойз до DLSSNR…')
            original.save(folder / 'denoise_source.png')
            receipt = denoise(folder / 'denoise_source.png', folder / 'denoised_source.png', config.denoise_strength,
                              backend=config.denoise_backend, device=config.device)
            report['denoise'].update(receipt)
            with Image.open(folder / 'denoised_source.png') as saved:
                model_image = saved.convert('RGB')
            if model_image.size != (width, height):
                model_image = model_image.resize((width, height), Image.Resampling.LANCZOS)
            model_image.save(folder / 'model_input.png')
            report.update(model_input='model_input.png', pipeline=[denoise_stage, 'resize', 'DLSSNR'])
        frame = np.asarray(model_image, dtype=np.uint8).copy()
        original = model_image = None
        write_json(folder / 'settings.json', report)
        model_start = 20 if active_denoise and config.denoise_position == 'before' else 10
        model_end = 78 if active_denoise and config.denoise_position == 'after' else 90
        emit('progress', percent=model_start - 2, message='Подготовка модели…', size=[width, height])
        if NATIVE_MODE:
            from native_backend import infer
            result, details = infer(frame, config.controls, folder, emit)
            report.update(details)
            frame = None
        else:
            os.environ['DLSSNR_COMPILE'] = '0'
            os.environ['DLSSNR_TRITON'] = '0'
            sys.path.insert(0, str(ROOT / 'dlssnr-pytorch'))
            import torch
            from dlssnr.io import Controls, save_image
            from dlssnr.model import NRNet
            from dlssnr.weights import Weights
            torch.set_num_threads(8)
            if config.device == 'cuda' and not torch.cuda.is_available():
                raise RuntimeError('CUDA недоступна. Выберите CPU или проверьте драйвер видеокарты.')
            weights = ROOT / 'Model/weights.safetensors'
            if not weights.is_file():
                raise FileNotFoundError('Не найдены веса: Model/weights.safetensors.')
            controls = Controls(**config.controls)
            report.update(controls=asdict(controls), torch=torch.__version__, weights=str(weights))
            if config.device == 'cuda':
                report['gpu'] = torch.cuda.get_device_name(0)
                report['initial_free_cuda_gib'] = torch.cuda.mem_get_info()[0] / 1024**3
                torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            net = NRNet(Weights.load(str(weights)), width, height).to(config.device).eval()
            if config.device == 'cuda':
                torch.cuda.synchronize()
            report['model_build_seconds'] = time.perf_counter() - started
            report['padded_size'] = [net.padding.width, net.padding.height]
            count, total = 0, len(net.layers)

            def step(_layer, _inputs, _outputs):
                nonlocal count
                count += 1
                if count % 3 == 0 or count == total:
                    emit('progress', percent=model_start + int((model_end - model_start) * count / total),
                         message='Обработка изображения…', layers_done=count, layers_total=total)

            hooks = [layer.register_forward_hook(step) for layer in net.layers]
            emit('progress', percent=model_start, message='Обработка изображения…', gpu=report.get('gpu', 'CPU'))
            started = time.perf_counter()
            result = net(frame, controls)
            if config.device == 'cuda':
                torch.cuda.synchronize()
            report['inference_seconds'] = time.perf_counter() - started
            if result.shape != frame.shape or result.dtype != np.uint8:
                raise RuntimeError('Модель вернула изображение с неверным размером или типом.')
            if config.device == 'cuda':
                report['peak_allocated_gib'] = torch.cuda.max_memory_allocated() / 1024**3
                report['peak_reserved_gib'] = torch.cuda.max_memory_reserved() / 1024**3
                report['memory_note'] = 'CUDA allocator statistics; not measurements of physical VRAM.'
            # A denoiser after DLSSNR must not compete with the model for VRAM.
            for handle in hooks:
                handle.remove()
            hooks.clear()
            net = frame = None
            gc.collect()
            if config.device == 'cuda':
                torch.cuda.empty_cache()
        if active_denoise and config.denoise_position == 'after':
            from denoise import denoise
            emit('progress', percent=80, message=f'{denoise_name} · денойз после DLSSNR…')
            Image.fromarray(result).save(folder / 'neural_output.png')
            receipt = denoise(folder / 'neural_output.png', folder / 'denoised_output.png', config.denoise_strength,
                              backend=config.denoise_backend, device=config.device)
            report['denoise'].update(receipt)
            report.update(neural_output='neural_output.png', pipeline=['resize', 'DLSSNR', denoise_stage])
            with Image.open(folder / 'denoised_output.png') as saved:
                result = np.asarray(saved.convert('RGB')).copy()
        emit('progress', percent=94, message='Сохранение результата…')
        Image.fromarray(result).save(folder / 'output.png')
        with Image.open(folder / 'output.png') as saved:
            if saved.size != (width, height) or saved.mode != 'RGB':
                raise RuntimeError('Не удалось проверить сохранённый PNG.')
            saved.verify()
        result_image = Image.fromarray(result)
        thumbnail_size = (1000, max(1, round(1000 * height / width)))
        # Portrait images also fit comfortably on the comparison sheet.
        if thumbnail_size[1] > 800:
            thumbnail_size = (max(1, round(800 * width / height)), 800)
        a = image.resize(thumbnail_size, Image.Resampling.LANCZOS)
        b = result_image.resize(thumbnail_size, Image.Resampling.LANCZOS)
        sheet = Image.new('RGB', (a.width * 2 + 48, a.height + 70), '#171c25')
        draw = ImageDraw.Draw(sheet)
        try:
            font = ImageFont.load_default(size=21)
        except TypeError:
            font = ImageFont.load_default()
        draw.text((16, 16), f'Input | {width} x {height}', fill='white', font=font)
        draw.text((32 + a.width, 16), 'DLSSNR result', fill='white', font=font)
        sheet.paste(a, (16, 54))
        sheet.paste(b, (32 + a.width, 54))
        sheet.save(folder / 'comparison.png')
        thumbnail = b.copy()
        thumbnail.thumbnail((360, 220), Image.Resampling.LANCZOS)
        thumbnail.save(folder / 'thumbnail.png')
        report.update(status='passed', output='output.png', comparison='comparison.png',
                      thumbnail='thumbnail.png',
                      processing_seconds=report['inference_seconds'] + report['denoise'].get('seconds', 0),
                      finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(folder / 'settings.json', report)
        emit('complete', percent=100, folder=str(folder), size=[width, height],
             inference_seconds=report['inference_seconds'], processing_seconds=report['processing_seconds'],
             gpu=report.get('gpu', 'CPU'))
        return 0
    except Exception as error:
        message = str(error)
        if torch is not None and isinstance(error, torch.cuda.OutOfMemoryError):
            message = 'Не хватило памяти видеокарты. Уменьшите разрешение или закройте другие программы и повторите запуск.'
        report.update(status='failed', error=f'{type(error).__name__}: {error}',
                      finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(folder / 'settings.json', report)
        emit('error', message=message, details=traceback.format_exc())
        return 1
    finally:
        for handle in hooks:
            handle.remove()
        net = frame = result = None
        gc.collect()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    args = parser.parse_args()
    sys.exit(process(args.request))
