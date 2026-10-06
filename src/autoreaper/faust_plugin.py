"""Install the "Faust (AutoReaper)" CLAP plugin from this version's GitHub release.

The release (tag faust-plugin-v<VERSION>, made by .github/workflows/package.yml) holds one zip per
system with the plugin, libfaust and the Faust libraries, and SHA256SUMS.txt. Installing downloads the
zip for REAPER's system, checks its checksum and swaps it into the user's CLAP folder, where REAPER finds
it on its next plug-in scan. Nothing else is needed on the computer except, on Windows, the Visual C++
runtime most computers have.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx

NAME = 'AutoReaper Faust'
# The plugin version this server works with; plugin/CMakeLists.txt has the same (a test checks).
VERSION = '0.1.1'
RELEASES = 'https://github.com/onliner10/AutoReaper/releases/download'
TARGETS = ('windows-x64', 'macos-arm64', 'macos-x64', 'linux-x64')
VC_REDIST = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
STAGING = '.autoreaper-faust-'  # prefix of temporary folders beside the installed plugin


class InstallError(RuntimeError):
    pass


def release_url(version: str = VERSION) -> str:
    """Where the zips are; AUTOREAPER_FAUST_PLUGIN_URL points elsewhere (a mirror, or a test server)."""
    return (os.environ.get('AUTOREAPER_FAUST_PLUGIN_URL') or f'{RELEASES}/faust-plugin-v{version}').rstrip('/')


def target_of(reaper_version: str | None = None) -> str:
    """The package for the REAPER that runs, from reaper.GetAppVersion() ("7.81/macOS-arm64", "7.81/x64",
    "7.81/linux-x86_64"), else for this computer."""
    build = (reaper_version or '').partition('/')[2].lower()
    if build:
        system, arm = '', 'arm64' in build or 'aarch64' in build
        if build in ('win32', 'x86') or re.fullmatch(r'linux-i\d86', build):
            system = '32-bit'
        elif 'linux' in build:
            system = 'linux'
        elif 'mac' in build or 'osx' in build:
            system = 'macos'
        elif build == 'arm64ec':
            system, arm = 'windows', False  # an ARM64EC REAPER loads x64 plugins
        elif build in ('x64', 'win64'):
            system = 'windows'
        if system:
            name = f'{system}-{"arm64" if arm else "x64"}'
            if name not in TARGETS:
                raise InstallError(f'No package for REAPER {reaper_version}: there are packages for '
                                   f'{", ".join(TARGETS)}. Build the plugin from plugin/ instead.')
            return name
    machine = platform.machine().lower()
    arm = machine in ('arm64', 'aarch64')
    if sys.platform == 'darwin':
        # A Python under Rosetta reports x86_64 on Apple silicon.
        arm = arm or subprocess.run(['sysctl', '-n', 'hw.optional.arm64'], capture_output=True,
                                    text=True).stdout.strip() == '1'
        name = f'macos-{"arm64" if arm else "x64"}'
    elif sys.platform == 'win32':
        name = 'windows-arm64' if arm else 'windows-x64'
    else:
        name = 'linux-arm64' if arm else 'linux-x64'
    if name not in TARGETS:
        raise InstallError(f'No package for {name}: there are packages for {", ".join(TARGETS)}. '
                           'Build the plugin from plugin/ instead.')
    return name


def clap_folder(target: str) -> Path:
    """The per-user folder REAPER scans for CLAP plugins (no administrator rights needed)."""
    if target.startswith('windows'):
        local = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(local) / 'Programs' / 'Common' / 'CLAP'
    if target.startswith('macos'):
        return Path.home() / 'Library' / 'Audio' / 'Plug-Ins' / 'CLAP'
    return Path.home() / '.clap'


def installed_item(target: str, folder: Path) -> Path:
    """What the package puts in the CLAP folder: the bundle on macOS, a folder elsewhere."""
    return folder / (f'{NAME}.clap' if target.startswith('macos') else NAME)


def installed_version(target: str, folder: Path) -> str | None:
    item = installed_item(target, folder)
    notes = item / 'Contents' / 'Resources' / 'INSTALL.txt' if target.startswith('macos') else item / 'INSTALL.txt'
    try:
        found = re.match(rf'{NAME} (\S+):', notes.read_text(encoding='utf-8'))
    except OSError:
        return 'unknown' if item.exists() else None
    return found.group(1) if found else 'unknown'


def other_copies(target: str, folder: Path) -> list[str]:
    """Other files in the CLAP folder that are this plugin (a build copied by hand): REAPER would list it twice."""
    item = installed_item(target, folder)
    if not folder.is_dir():
        return []
    return [str(path) for path in folder.rglob(f'{NAME}.clap')
            if path != item and item not in path.parents and STAGING not in str(path)]


def missing_vc_runtime() -> list[str]:
    """The Visual C++ runtime DLLs libfaust needs that Windows lacks."""
    if sys.platform != 'win32':
        return []
    system = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
    return [dll for dll in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll') if not (system / dll).is_file()]


def download(client: httpx.Client, url: str, destination: Path) -> None:
    try:
        with client.stream('GET', url) as response:
            if response.status_code == 404:
                raise InstallError(f'{url} does not exist (404): this plugin version has no release yet.')
            response.raise_for_status()
            with destination.open('wb') as file:
                for chunk in response.iter_bytes(1 << 16):
                    file.write(chunk)
    except httpx.HTTPError as error:
        raise InstallError(f'Download of {url} failed: {error}') from error


def checksum(sums: str, name: str) -> str:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == name:
            return parts[0].lower()
    raise InstallError(f'SHA256SUMS.txt of the release has no line for {name}.')


def extract(archive: Path, destination: Path) -> None:
    if sys.platform == 'darwin':
        # ditto keeps the bundle's permissions and signature as packaged.
        subprocess.run(['ditto', '-x', '-k', str(archive), str(destination)], check=True, capture_output=True)
        return
    with zipfile.ZipFile(archive) as zip_file:
        root = destination.resolve()
        for info in zip_file.infolist():
            path = (destination / info.filename).resolve()
            if root not in path.parents:
                raise InstallError(f'Unexpected path in the package: {info.filename}')
            zip_file.extract(info, destination)
            mode = info.external_attr >> 16 & 0o777
            if mode and not info.is_dir():
                path.chmod(mode)


def swap_in(new: Path, item: Path, staging: Path) -> None:
    """Replace item by new with renames, so REAPER never sees half a plugin; a copy REAPER has loaded keeps
    working from its old files until REAPER restarts."""
    old = staging / 'old'
    try:
        if item.exists():
            item.rename(old)
        new.rename(item)
    except OSError as error:
        if old.exists() and not item.exists():
            old.rename(item)
        raise InstallError(f'Could not replace {item}: {error}. If REAPER has the plugin loaded (Windows locks '
                           'it), close REAPER and install again.') from error


def remove_staging(folder: Path) -> None:
    """Delete temporary folders of this and earlier installs (Windows keeps files a running REAPER loaded)."""
    for path in folder.glob(STAGING + '*'):
        shutil.rmtree(path, ignore_errors=True)


def install(target: str, folder: Path | None = None, force: bool = False, version: str = VERSION) -> dict:
    """Download and install the package for target into folder (default: the per-user CLAP folder)."""
    if target not in TARGETS:
        raise InstallError(f'Unknown target {target!r}; one of {", ".join(TARGETS)}.')
    folder = Path(folder) if folder else clap_folder(target)
    item = installed_item(target, folder)
    libraries = item / 'Contents' / 'Resources' / 'faustlibraries' if target.startswith('macos') else item / 'faustlibraries'
    result = {'target': target, 'version': version, 'installed': str(item), 'faust_libraries': str(libraries)}
    before = installed_version(target, folder)
    if before == version and not force:
        return {**result, 'ok': True, 'changed': False, 'note': f'Version {version} is already installed.'}
    folder.mkdir(parents=True, exist_ok=True)
    remove_staging(folder)
    base = release_url(version)
    zip_name = f'autoreaper-faust-{target}.zip'
    staging = folder / f'{STAGING}{uuid4().hex[:8]}'
    staging.mkdir()
    try:
        with tempfile.TemporaryDirectory() as temporary, httpx.Client(follow_redirects=True, timeout=60) as client:
            archive = Path(temporary) / zip_name
            sums = Path(temporary) / 'SHA256SUMS.txt'
            download(client, f'{base}/SHA256SUMS.txt', sums)
            download(client, f'{base}/{zip_name}', archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if digest != checksum(sums.read_text(encoding='utf-8'), zip_name):
                raise InstallError(f'{zip_name} does not match its SHA-256 in the release; nothing was installed.')
            extract(archive, staging / 'unpacked')
        new = staging / 'unpacked' / NAME
        if target.startswith('macos'):
            new = new / f'{NAME}.clap'
        if not new.exists():
            raise InstallError(f'The package has no {new.relative_to(staging / "unpacked")}.')
        swap_in(new, item, staging)
    finally:
        remove_staging(folder)
    result.update(ok=True, changed=True, replaced=before)
    duplicates = other_copies(target, folder)
    if duplicates:
        result['warning'] = ('Other copies of the plugin are in the CLAP folder; REAPER lists each. Remove them: '
                             + ', '.join(duplicates))
    missing = missing_vc_runtime() if target.startswith('windows') else []
    if missing:
        result['vc_runtime_missing'] = missing
        result['vc_runtime'] = f'The plugin needs the Microsoft Visual C++ Redistributable (x64): {VC_REDIST}'
    return result
