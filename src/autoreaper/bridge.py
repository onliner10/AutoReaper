"""File mailbox to the AutoReaper Bridge script running inside REAPER.

One request at a time: the server writes ``request.lua``, the bridge claims it,
runs it inside REAPER's main thread and writes ``<id>.json``. A request that
times out has an unknown outcome and is never replayed.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

LUA = Path(__file__).with_name('lua')
BRIDGE_SCRIPT_NAME = 'AutoReaper Bridge.lua'
# The bridge protocol this server speaks; the heartbeat reports the script's.
PROTOCOL = 3
STALE_SECONDS = 5


def home_directory() -> Path:
    """AUTOREAPER_HOME, else ~/.autoreaper (the bridge script uses the same rule)."""
    return Path(os.environ.get('AUTOREAPER_HOME') or Path.home() / '.autoreaper')


def mailbox_directory() -> Path:
    return Path(os.environ.get('AUTOREAPER_MAILBOX') or home_directory() / 'bridge')


def default_reaper_resource_path() -> Path:
    """REAPER's default resource folder per platform (Options > Show REAPER resource path)."""
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / 'REAPER'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'REAPER'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'REAPER'


def install_bridge_script(resource_path: Path | None = None) -> Path:
    """Copy the bridge script into REAPER's Scripts folder and return its path."""
    resource = Path(resource_path) if resource_path else default_reaper_resource_path()
    if not resource.is_dir():
        raise FileNotFoundError(
            f'No REAPER resource folder at {resource}. In REAPER use Options > Show REAPER resource path '
            'in explorer/finder and pass that folder.')
    destination = resource / 'Scripts' / BRIDGE_SCRIPT_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LUA / 'bridge.lua', destination)
    return destination


def lua_string(value: str) -> str:
    # Byte escapes preserve UTF-8 and cannot terminate a Lua literal.
    return '"' + ''.join('\\%03d' % b for b in value.encode('utf-8')) + '"'


class BridgeError(RuntimeError):
    pass


NOT_RUNNING = ('The AutoReaper Bridge is not running in REAPER. Install it with the install_bridge tool, '
               'then in REAPER run Actions > Show action list > New action > Load ReaScript, pick '
               f'"{BRIDGE_SCRIPT_NAME}" from the Scripts folder and run it.')


class ReaperBridge:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else mailbox_directory()

    def status(self):
        # Windows Lua replaces the heartbeat with remove+rename. A read can land
        # in that brief gap; retry the status read only, never a submitted edit.
        self.directory.mkdir(parents=True, exist_ok=True)
        for attempt in range(5):
            try:
                status = json.loads((self.directory / 'heartbeat.json').read_text(encoding='utf-8'))
                break
            except (OSError, ValueError) as exc:
                if attempt == 4:
                    raise BridgeError(NOT_RUNNING) from exc
                time.sleep(.02)
        if time.time() - status['timestamp'] > STALE_SECONDS:
            raise BridgeError('The REAPER bridge is stopped or busy (stale heartbeat). If REAPER is open, '
                              'run the AutoReaper Bridge action again. Do not retry an uncertain edit.')
        return status

    async def evaluate(self, code, *, project_id='', label='AutoReaper edit', mutate=True, timeout=60,
                       preflight=''):
        return await asyncio.to_thread(self._evaluate, code, project_id, label, mutate, timeout, preflight)

    def _evaluate(self, code, project_id, label, mutate, timeout, preflight):
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.directory / 'client.lock'), timeout=timeout):
            status = self.status()
            if (self.directory / 'request.running').exists() or (self.directory / 'request.lua').exists():
                raise BridgeError('An earlier request is pending or has an uncertain outcome; '
                                  'read its receipt before continuing.')
            if mutate and not project_id:
                raise BridgeError('A fresh project_id from inspect_project is required for edits.')
            request_id = uuid4().hex
            response_path = self.directory / (request_id + '.json')
            body = ('return {id=%s, code=%s, preflight=%s, project_id=%s, label=%s, mutate=%s, session=%s, '
                    'timeout=%s}' % (
                        lua_string(request_id), lua_string(code), lua_string(preflight or ''),
                        lua_string(project_id), lua_string(label), str(bool(mutate)).lower(),
                        lua_string(status['session']), repr(timeout)))
            temporary = self.directory / 'request.tmp'
            temporary.write_text(body, encoding='utf-8')
            os.replace(temporary, self.directory / 'request.lua')
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if response_path.exists():
                    try:
                        receipt = json.loads(response_path.read_text(encoding='utf-8'))
                    except (PermissionError, FileNotFoundError, json.JSONDecodeError):
                        # The Lua writer can still hold the receipt on Windows.
                        # Retry reading the same receipt, never submit again.
                        time.sleep(.04)
                        continue
                    response_path.unlink(missing_ok=True)
                    return receipt
                time.sleep(.04)
            raise BridgeError(f'Request {request_id} timed out; outcome unknown. Do not replay it. '
                              f'read_receipt with request_id={request_id} shows the result once it finishes.')

    def reload(self, timeout=5.0):
        """Ask the running bridge to reload its script file; the new session's status, or None."""
        try:
            before = self.status()['session']
        except BridgeError:
            return None
        (self.directory / 'reload.request').write_text('reload', encoding='utf-8')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(.1)
            try:
                status = self.status()
            except BridgeError:
                continue
            if status['session'] != before:
                return status
        (self.directory / 'reload.request').unlink(missing_ok=True)
        raise BridgeError('The running bridge did not reload (an older version cannot). In REAPER run the '
                          'AutoReaper Bridge action again and choose "New instance".')

    def receipt(self, request_id):
        if not request_id.isalnum():
            raise ValueError('request_id is the hex id from a timed-out request.')
        path = self.directory / (request_id + '.json')
        if not path.exists():
            running = (self.directory / 'request.running').exists() or (self.directory / 'request.lua').exists()
            return {'ok': False, 'found': False, 'still_running': running,
                    'error': 'No receipt yet.' if running else 'No receipt for that id.'}
        receipt = json.loads(path.read_text(encoding='utf-8'))
        path.unlink(missing_ok=True)
        return {**receipt, 'found': True}
