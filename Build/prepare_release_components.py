"""Stage pinned public release components for the portable build, inside the checkout."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'Reference/MaxNeuralStudio'


def download(url, path, expected):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        request = urllib.request.Request(url, headers={'User-Agent': 'DLSSNR-portable-build'})
        with urllib.request.urlopen(request, timeout=120) as source, path.open('xb') as target:
            while chunk := source.read(1024*1024):
                target.write(chunk)
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError(f'Component checksum mismatch: {path.name}')


def main():
    archive = REFERENCE / 'downloads/dlss5-video-player-v0.26.2-win64.zip'
    download('https://github.com/2600th/dlss5-video-player/releases/download/'
             'dlss5-video-player-v0.26.2/dlss5-video-player-v0.26.2-win64.zip', archive,
             'eab061ccfb6d6ba62444bb4aae77b29afd20895ff1812e92490ade0a51c0462f')
    destination = REFERENCE / 'runtime-0.26.2'
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if not (destination/item.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError('Unsafe runtime path')
        package.extractall(destination)
    optix = REFERENCE / 'optix-3.0'
    download('https://github.com/DeclanRussell/NvidiaAIDenoiser/releases/download/3.0/Denoiser.exe',
             optix / 'Denoiser.exe', '42dd0a099a3029f3271aa42a782aee06bbfd03bd2f466a2ecfa76c8666616058')
    # The MIT notice is tracked next to the recipe, independently of upstream changes.
    shutil.copy2(ROOT / 'Build/vendor-notices/NvidiaAIDenoiser-LICENSE', optix / 'LICENSE')
    from prepare_oidn import prepare
    prepare()
    print('Pinned native runtime, OptiX and OIDN ready.', flush=True)


if __name__ == '__main__':
    main()
