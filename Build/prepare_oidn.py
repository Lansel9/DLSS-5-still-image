"""Stage the pinned official OIDN Windows archive entirely inside this project."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'Reference/OIDN'
VERSION = '2.5.1'
NAME = f'oidn-{VERSION}.x64.windows.zip'
URL = f'https://github.com/RenderKit/oidn/releases/download/v{VERSION}/{NAME}'
SHA256 = 'f11f91bc072a5e3a564515724cb72ab8fcfbc445c84a84c197ff9b16cc01396f'
SIZE = 56619412


def prepare():
    FOLDER.mkdir(parents=True, exist_ok=True)
    archive = FOLDER / NAME
    if not archive.exists():
        temporary = archive.with_suffix('.download')
        request = urllib.request.Request(URL, headers={'User-Agent': 'DLSSNR-local-app'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(archive)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if archive.stat().st_size != SIZE or digest != SHA256:
        raise ValueError('Official OIDN archive size/SHA256 mismatch; nothing extracted.')
    with zipfile.ZipFile(archive) as package:
        # Resolve every destination before extraction; never write outside our folder.
        for entry in package.infolist():
            destination = (FOLDER / entry.filename).resolve()
            if not destination.is_relative_to(FOLDER.resolve()) or ':' in entry.filename:
                raise ValueError(f'Unsafe archive path: {entry.filename}')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symbolic links are not accepted.')
        package.extractall(FOLDER)
    files = {}
    for path in sorted((FOLDER / f'oidn-{VERSION}.x64.windows/bin').glob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                files[path.relative_to(FOLDER).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    if not files:
        raise ValueError('No OIDN binaries found after extraction.')
    manifest = dict(version=VERSION, url=URL, archive=NAME, archive_size=SIZE,
                    archive_sha256=SHA256, files_sha256=files)
    (FOLDER / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == '__main__':
    prepare()
