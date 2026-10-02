"""Runs the real bridge.lua and read-only sandbox in Lua 5.4 against a fake REAPER API."""
import asyncio
import json
import os
import threading
import time

import pytest

from autoreaper import server
from autoreaper.bridge import LUA, BridgeError, ReaperBridge

lupa = pytest.importorskip('lupa')
try:
    from lupa import lua54 as lua_module
except ImportError:  # pragma: no cover
    lua_module = lupa


class FakeReaper:
    """Starts bridge.lua in its own thread and runs its defer loop until stopped."""

    def __init__(self, mailbox):
        self.mailbox = mailbox
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        lua = lua_module.LuaRuntime(unpack_returned_tuples=True)
        state = {'changes': 0, 'deferred': None}
        reaper = lua.eval('{}')
        reaper.RecursiveCreateDirectory = lambda path, _: os.makedirs(path, exist_ok=True) or 1
        reaper.time_precise = time.perf_counter
        reaper.genGuid = lambda *_: '{SESSION}'
        reaper.GetAppVersion = lambda: '7.0/test'
        project = lua.eval('{}')
        reaper.EnumProjects = lambda *_: (project, 'C:/x/song.rpp')
        reaper.GetProjectStateChangeCount = lambda *_: state['changes']
        reaper.CountTracks = lambda *_: 3
        reaper.SetMediaTrackInfo_Value = lambda *_: state.__setitem__('changes', state['changes'] + 1)
        reaper.Undo_BeginBlock2 = reaper.Undo_EndBlock2 = reaper.UpdateArrange = lambda *_: None
        reaper.Undo_CanUndo2 = lambda *_: None
        reaper.atexit = lambda *_: None
        reaper.defer = lambda fn: state.__setitem__('deferred', fn)
        lua.globals().reaper = reaper
        # The C runtime's environment is not Python's os.environ on Windows.
        environment = {'AUTOREAPER_MAILBOX': str(self.mailbox)}
        lua.globals().os.getenv = lambda name: environment.get(name)
        lua.execute((LUA / 'bridge.lua').read_text(encoding='utf-8'))
        self.ready.set()
        while not self.stop.is_set() and state['deferred'] is not None:
            fn, state['deferred'] = state['deferred'], None
            fn()
            time.sleep(.005)

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(5)
        time.sleep(.05)
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(5)


def test_bridge_lua_runs_reads_edits_and_reports_errors(tmp_path):
    client = ReaperBridge(tmp_path)
    with FakeReaper(tmp_path):
        assert client.status()['protocol'] == server.PROTOCOL
        read = asyncio.run(client.evaluate('return {n=reaper.CountTracks(0)}', mutate=False))
        assert read['ok'] and read['outcome'] == 'read_only' and read['result'] == {'n': 3}

        edit = asyncio.run(client.evaluate('reaper.SetMediaTrackInfo_Value(nil, "D_VOL", 1) return true',
                                           project_id=read['project_id'], label='t'))
        assert edit['ok'] and edit['changed'] and edit['outcome'] == 'dispatched_unverified'

        wrong = asyncio.run(client.evaluate('return 1', project_id='other', label='t'))
        assert not wrong['ok'] and wrong['outcome'] == 'not_dispatched'

        failed = asyncio.run(client.evaluate('error("boom")', mutate=False))
        assert not failed['ok'] and 'boom' in failed['error']

        # Read-only sandbox: getters run, setters ask for write access.
        sandbox = asyncio.run(client.evaluate(server.readonly_program('return reaper.CountTracks(0)'), mutate=False))
        assert sandbox['result'] == {'status': 'complete', 'value': 3}
        blocked = asyncio.run(client.evaluate(
            server.readonly_program('reaper.SetMediaTrackInfo_Value(nil, "D_VOL", 1)'), mutate=False))
        assert blocked['result'] == {'status': 'needs_review', 'blocked': 'reaper.SetMediaTrackInfo_Value'}
        undo = asyncio.run(client.evaluate(server.readonly_program('return reaper.Undo_CanUndo2(0) == nil'), mutate=False))
        assert undo['result'] == {'status': 'complete', 'value': True}
        escape = asyncio.run(client.evaluate(server.readonly_program('return os.getenv("HOME")'), mutate=False))
        assert escape['result']['status'] == 'needs_review'

        # A garbage request is dropped instead of blocking every later one.
        (tmp_path / 'request.lua').write_text('return nil', encoding='utf-8')
        time.sleep(.1)
        assert not (tmp_path / 'request.running').exists()
        assert asyncio.run(client.evaluate('return 2', mutate=False))['result'] == 2
    assert not [p for p in tmp_path.glob('*.json') if p.name != 'heartbeat.json']


def test_status_reports_a_stopped_bridge(tmp_path):
    (tmp_path / 'heartbeat.json').write_text(json.dumps({'session': 'S', 'timestamp': time.time() - 60}))
    with pytest.raises(BridgeError, match='stopped or busy'):
        ReaperBridge(tmp_path).status()
