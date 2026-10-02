"""inspect.lua's filters against a fake project, in Lua 5.4 (as in REAPER)."""
import pytest

from autoreaper.bridge import LUA, lua_string

lupa = pytest.importorskip('lupa')
try:
    from lupa import lua54 as lua_module
except ImportError:  # pragma: no cover
    lua_module = lupa

# Two tracks at 120 BPM 4/4 (a bar is 2 s); Kick has one-bar items in bars 1-8, Lead Synth none.
FAKE = r'''
local tracks = {{name = 'Kick', items = {}}, {name = 'Lead Synth', items = {}}}
for bar = 1, 8 do tracks[1].items[bar] = {position = (bar - 1) * 2, length = 2} end
reaper = {
  EnumProjects = function() return 'project', 'song.rpp' end,
  GetSet_LoopTimeRange2 = function() return 0, 0 end,
  TimeMap2_beatsToTime = function(_, _, measures) return measures * 2 end,
  CountTracks = function() return #tracks end,
  GetTrack = function(_, i) return tracks[i + 1] end,
  GetTrackName = function(t) return true, t.name end,
  GetTrackGUID = function(t) return '{' .. t.name .. '}' end,
  GetMediaTrackInfo_Value = function() return 0 end,
  TrackFX_GetCount = function() return 0 end,
  CountTrackMediaItems = function(t) return #t.items end,
  GetTrackMediaItem = function(t, j) return t.items[j + 1] end,
  GetMediaItemInfo_Value = function(item, key) return key == 'D_POSITION' and item.position or item.length end,
  GetSetMediaItemInfo_String = function() return true, '{ITEM}' end,
  GetActiveTake = function() return nil end,
  CountProjectMarkers = function() return 0, 0, 0 end,
  GetCursorPositionEx = function() return 0 end,
  GetProjectLength = function() return 16 end,
  Master_GetTempo = function() return 120 end,
  GetProjectStateChangeCount = function() return 1 end,
  GetPlayStateEx = function() return 0 end,
}
'''


def inspect(track_query='', include_items=True, from_bar=None, to_bar=None):
    lua = lua_module.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(FAKE)
    nil = lambda v: 'nil' if v is None else str(v)
    code = (f'local include_notes=false local include_items={str(include_items).lower()} '
            f'local track_query={lua_string(track_query)} local from_bar={nil(from_bar)} local to_bar={nil(to_bar)}\n'
            + (LUA / 'inspect.lua').read_text(encoding='utf-8'))
    result = lua.execute(code)
    return [(t['name'], [item['index'] for item in t['items'].values()]) for t in result['tracks'].values()]


def test_inspect_filters_tracks_and_items():
    assert inspect() == [('Kick', list(range(8))), ('Lead Synth', [])]
    assert inspect(track_query='lead') == [('Lead Synth', [])]
    # Bars 3..5 (exclusive): items of bars 3 and 4 only, not the ones touching the edges.
    assert inspect(track_query='kick', from_bar=3, to_bar=5) == [('Kick', [2, 3])]
    assert inspect(include_items=False) == [('Kick', []), ('Lead Synth', [])]
