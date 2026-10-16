"""Build a clean Windows x64 native-NR folder; no local preferences or user images."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'Reference/MaxNeuralStudio'
PACKAGE = REFERENCE / 'runtime-0.26.2/DLSSVideoPlayer-v0.26.2-win64'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build():
    output = ROOT / 'Dist' / datetime.now().strftime('%Y%m%d_%H%M%S_%f') / 'DLSSNR-Portable'
    output.mkdir(parents=True, exist_ok=False)
    for path in (ROOT / 'App').glob('*.py'):
        copy(path, output / 'App' / path.name)
    python = ROOT / 'Build/cache/python'
    if not (python / 'python.exe').is_file():
        raise RuntimeError('Run Build/prepare_python.py first.')
    def ignore(_folder, names):
        return [name for name in names if name == '__pycache__' or name in ('pip', 'ensurepip', 'test', 'tests', 'idlelib')
                or name.endswith(('.pyc', '.pdb', '.lib', '.exp')) or name.startswith('pip-')]
    shutil.copytree(python, output / 'Python', ignore=ignore)
    (output / 'Python/python311._pth').write_text('.\nDLLs\nLib\nLib/site-packages\n../App\nimport site\n', encoding='utf-8')
    native = output / 'Runtime/Native'
    lock = json.loads((REFERENCE / 'runtime-lock-0.26.2.json').read_text(encoding='utf-8-sig'))
    for item in lock['entries']:
        source = PACKAGE / 'neural-runtime' / item['destination']
        if digest(source).lower() != item['sha256'].lower():
            raise ValueError(f'Wrong pinned runtime: {source.name}')
        copy(source, native / 'neural-runtime' / source.name)
    for name in ('NeuralWorker.exe', 'ReShade.ini', 'ReShadePreset.ini'):
        copy(PACKAGE / 'neural-runtime' / name, native / 'neural-runtime' / name)
    for name in ('ffmpeg.exe', 'ffprobe.exe', 'LICENSE', 'THIRD_PARTY.md', 'EXPERIMENTAL_RUNTIME_NOTICE.txt'):
        copy(PACKAGE / name, native / name)
    shutil.copytree(PACKAGE / 'THIRD_PARTY_LICENSES', native / 'THIRD_PARTY_LICENSES')
    # App-local CRT: the recipient does not need a separate VC++ installation.
    crt = [(python / 'vcruntime140.dll', 'vcruntime140.dll'),
           (python / 'vcruntime140_1.dll', 'vcruntime140_1.dll'),
           (next((python / 'Lib/site-packages/numpy.libs').glob('msvcp140-*.dll')), 'msvcp140.dll')]
    for source, name in crt:
        copy(source, native / 'CRT' / name)
    runtime_files = [p for p in native.rglob('*') if p.is_file() and p.suffix.lower() in ('.exe', '.dll', '.addon64')]
    (native / 'manifest.json').write_text(json.dumps({'sha256': {
        p.relative_to(native).as_posix(): digest(p) for p in runtime_files}}, indent=2), encoding='utf-8')
    for name in ('Denoiser.exe', 'LICENSE'):
        copy(REFERENCE / 'optix-3.0' / name, output / 'Reference/MaxNeuralStudio/optix-3.0' / name)
    oidn = ROOT / 'Reference/OIDN'
    copy(oidn / 'manifest.json', output / 'Reference/OIDN/manifest.json')
    shutil.copytree(oidn / 'oidn-2.5.1.x64.windows', output / 'Reference/OIDN/oidn-2.5.1.x64.windows')
    for source, name in crt:
        copy(source, output / 'Reference/OIDN/oidn-2.5.1.x64.windows/bin' / name)
    (output / 'portable.json').write_text(json.dumps({'version': '0.0.0', 'engine': 'native', 'platform': 'Windows x64',
                                                     'runtime': '310.8.SF-v2', 'python': '3.11.17',
                                                     'requires': 'NVIDIA RTX + compatible NVIDIA driver'}, indent=2), encoding='utf-8')
    (output / 'Results').mkdir()
    (output / 'DLSSNR.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\n"%~dp0Python\\python.exe" -I -B -X utf8 "%~dp0App\\launch.py"\r\n', encoding='ascii')
    copy(ROOT / 'Build/README_PORTABLE.txt', output / 'ПРОЧИТАЙ МЕНЯ.txt')
    copy(ROOT / 'Build/cache/python-source.json', output / 'Runtime/python-source.json')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT / 'Build/compile_launcher.ps1'), '-Target', str(output)], check=True)
    print(output, flush=True)
    (ROOT / 'Build/latest.json').write_text(json.dumps({'folder': str(output)}, indent=2), encoding='utf-8')
    return output


def package(folder):
    archive = folder.parent / 'DLSSNR-Portable-0.0.0-Windows-x64.zip'
    # Runtime logs and preferences generated during QA never enter the transfer archive.
    def included(path):
        rel = path.relative_to(folder)
        return (rel.parts[0] != 'Results' and path.name not in ('preferences.json', 'session.lock', 'launch.log', 'SHA256.json')
                and '__pycache__' not in rel.parts and path.suffix.lower() not in ('.log', '.pyc', '.dmp')
                and 'ngx_logs' not in rel.parts)
    files = [p for p in folder.rglob('*') if p.is_file() and included(p)]
    manifest = {'version': '0.0.0', 'files': {p.relative_to(folder).as_posix(): digest(p) for p in files}}
    (folder / 'SHA256.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    files.append(folder / 'SHA256.json')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=7) as target:
        for p in files:
            target.write(p, Path(folder.name) / p.relative_to(folder))
    info = dict(folder=str(folder), zip=str(archive), bytes=archive.stat().st_size,
                unpacked_bytes=sum(p.stat().st_size for p in files), sha256=digest(archive), files=len(files))
    (folder.parent / 'build.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    print(json.dumps(info, indent=2), flush=True)
    return info


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path)
    args = parser.parse_args()
    if args.package:
        folder = args.package.resolve()
        if not folder.is_relative_to((ROOT / 'Dist').resolve()):
            raise ValueError('Package must be inside project Dist.')
        package(folder)
    else:
        build()
