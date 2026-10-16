"""Fetch a pinned, relocatable Windows Python and two binary wheels into Build/cache."""
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'Build/cache'
PYTHON_URL = ('https://github.com/astral-sh/python-build-standalone/releases/download/20261013/'
              'cpython-3.11.17%2B20261013-x86_64-pc-windows-msvc-install_only_stripped.tar.gz')
PYTHON_SHA256 = '54a38c5e7cc1a5fd2b0182b495f29606dc1455d18ebfd3b5bf0a3c09253b5031'


def fetch(url, path, expected):
    if not path.exists():
        request = urllib.request.Request(url, headers={'User-Agent': 'DLSSNR-portable-build'})
        with urllib.request.urlopen(request, timeout=90) as source, path.open('xb') as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f'SHA256 mismatch: {path.name}')
    return dict(url=url, sha256=actual, bytes=path.stat().st_size)


def prepare():
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = {'python': fetch(PYTHON_URL, CACHE / 'python.tar.gz', PYTHON_SHA256), 'wheels': []}
    python = CACHE / 'python'
    if not python.exists():
        with tarfile.open(CACHE / 'python.tar.gz') as archive:
            for entry in archive.getmembers():
                destination = (CACHE / entry.name).resolve()
                if not destination.is_relative_to(python.resolve()) or entry.issym() or entry.islnk():
                    raise ValueError(f'Unsafe archive entry: {entry.name}')
            archive.extractall(CACHE, filter='data')
    site = python / 'Lib/site-packages'
    site.mkdir(parents=True, exist_ok=True)
    for name, version in [('numpy', '2.2.6'), ('pillow', '11.3.0')]:
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=30) as stream:
            metadata = json.load(stream)
        wheel = next(item for item in metadata['urls']
                     if item['filename'].endswith('cp311-cp311-win_amd64.whl'))
        path = CACHE / wheel['filename']
        manifest['wheels'].append(fetch(wheel['url'], path, wheel['digests']['sha256']))
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if not (site / entry.filename).resolve().is_relative_to(site.resolve()):
                    raise ValueError('Unsafe wheel path')
            archive.extractall(site)
        print(f'{name} {version}: verified', flush=True)
    (python / 'python311._pth').write_text('.\nDLLs\nLib\nLib/site-packages\n../App\nimport site\n', encoding='utf-8')
    (CACHE / 'python-source.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(python, flush=True)


if __name__ == '__main__':
    prepare()
