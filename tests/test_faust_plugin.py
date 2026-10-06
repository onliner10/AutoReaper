"""install_faust_plugin's installer against a local web server holding fake packages."""
from __future__ import annotations

import hashlib
import http.server
import re
import sys
import threading
import zipfile
from functools import partial
from pathlib import Path

import pytest

from autoreaper import faust_plugin
from autoreaper.faust_plugin import NAME, InstallError, install, target_of

REPOSITORY = Path(__file__).resolve().parents[1]


def test_version_matches_the_plugin():
    cmake = (REPOSITORY / 'plugin' / 'CMakeLists.txt').read_text(encoding='utf-8')
    assert re.search(r'project\(\S+ VERSION (\S+)', cmake).group(1) == faust_plugin.VERSION


@pytest.mark.parametrize('reaper, target', [
    ('7.81/linux-x86_64', 'linux-x64'), ('7.81/macOS-arm64', 'macos-arm64'), ('7.22/OSX64', 'macos-x64'),
    ('7.81/x64', 'windows-x64'), ('7.81/arm64ec', 'windows-x64')])
def test_target_follows_the_running_reaper(reaper, target):
    assert target_of(reaper) == target


@pytest.mark.parametrize('reaper', ['7.81/win32', '7.81/linux-aarch64', '7.81/linux-i686'])
def test_no_package_for_other_reapers(reaper):
    with pytest.raises(InstallError, match='No package'):
        target_of(reaper)


def make_package(directory: Path, target: str, version: str, sums: str | None = None) -> None:
    """A zip laid out like plugin/package.py's, and SHA256SUMS.txt for it."""
    archive = directory / f'autoreaper-faust-{target}.zip'
    notes = f'{NAME} {version}: a CLAP plugin that runs Faust code, with Faust 2.88.0 included.\n'
    with zipfile.ZipFile(archive, 'w') as zip_file:
        if target.startswith('macos'):
            bundle = f'{NAME}/{NAME}.clap/Contents'
            zip_file.writestr(f'{bundle}/MacOS/{NAME}', 'binary ' + version)
            zip_file.writestr(f'{bundle}/Frameworks/libfaust.2.dylib', 'libfaust')
            zip_file.writestr(f'{bundle}/Resources/INSTALL.txt', notes)
            zip_file.writestr(f'{bundle}/Resources/faustlibraries/stdfaust.lib', 'library')
            zip_file.writestr(f'{NAME}/INSTALL.txt', notes)
        else:
            plugin = zipfile.ZipInfo(f'{NAME}/{NAME}.clap')
            plugin.external_attr = 0o755 << 16
            zip_file.writestr(plugin, 'binary ' + version)
            zip_file.writestr(f'{NAME}/libfaust.so.2', 'libfaust')
            zip_file.writestr(f'{NAME}/faustlibraries/stdfaust.lib', 'library')
            zip_file.writestr(f'{NAME}/INSTALL.txt', notes)
    digest = sums or hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A web server for tmp_path/release; the installer downloads from it."""
    root = tmp_path / 'release'
    root.mkdir()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv('AUTOREAPER_FAUST_PLUGIN_URL', f'http://127.0.0.1:{server.server_address[1]}/')
    for name in ('NO_PROXY', 'no_proxy'):
        monkeypatch.setenv(name, '127.0.0.1,localhost')
    yield root
    server.shutdown()


def test_installs_checks_and_upgrades(release, tmp_path):
    clap = tmp_path / 'clap'
    make_package(release, 'linux-x64', '0.1.0')
    result = install('linux-x64', clap, version='0.1.0')
    assert result['ok'] and result['changed'] and result['replaced'] is None
    folder = clap / NAME
    assert (folder / f'{NAME}.clap').read_text() == 'binary 0.1.0'
    assert (folder / 'faustlibraries' / 'stdfaust.lib').is_file()
    if sys.platform != 'win32':
        assert (folder / f'{NAME}.clap').stat().st_mode & 0o111
    assert sorted(p.name for p in clap.iterdir()) == [NAME]  # no staging left

    again = install('linux-x64', clap, version='0.1.0')
    assert again['ok'] and not again['changed']

    make_package(release, 'linux-x64', '0.2.0')
    upgrade = install('linux-x64', clap, version='0.2.0')
    assert upgrade['changed'] and upgrade['replaced'] == '0.1.0'
    assert (folder / f'{NAME}.clap').read_text() == 'binary 0.2.0'
    assert sorted(p.name for p in clap.iterdir()) == [NAME]


def test_macos_installs_the_bundle(release, tmp_path):
    clap = tmp_path / 'clap'
    make_package(release, 'macos-arm64', '0.1.0')
    result = install('macos-arm64', clap, version='0.1.0')
    assert result['changed'] and sorted(p.name for p in clap.iterdir()) == [f'{NAME}.clap']
    assert faust_plugin.installed_version('macos-arm64', clap) == '0.1.0'
    assert (clap / f'{NAME}.clap' / 'Contents' / 'Frameworks' / 'libfaust.2.dylib').is_file()


def test_a_wrong_checksum_installs_nothing(release, tmp_path):
    clap = tmp_path / 'clap'
    make_package(release, 'linux-x64', '0.1.0', sums='0' * 64)
    with pytest.raises(InstallError, match='SHA-256'):
        install('linux-x64', clap, version='0.1.0')
    assert list(clap.iterdir()) == []


def test_a_missing_release_says_so(release, tmp_path):
    with pytest.raises(InstallError, match='no release'):
        install('linux-x64', tmp_path / 'clap', version='0.1.0')


def test_warns_about_a_copy_installed_by_hand(release, tmp_path):
    clap = tmp_path / 'clap'
    clap.mkdir()
    (clap / f'{NAME}.clap').write_text('a build copied by hand')
    make_package(release, 'linux-x64', '0.1.0')
    result = install('linux-x64', clap, version='0.1.0')
    assert str(clap / f'{NAME}.clap') in result['warning']
