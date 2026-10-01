"""fx.lua's value resolution against a fake plugin, in Lua 5.4 (as in REAPER)."""
import pytest

from autoreaper.bridge import LUA
from autoreaper import server

lupa = pytest.importorskip('lupa')
try:
    from lupa import lua54 as lua_module
except ImportError:  # pragma: no cover
    lua_module = lupa

# A plugin with a log frequency (20 Hz..20 kHz), a linear gain (-30..+30 dB),
# a three-way mode list, an on/off switch and a ratio; plus a track to find it on.
FAKE = r'''
local values = {0.5, 0.5, 0, 0, 0.25}
local names = {'Frequency', 'Gain', 'Mode', 'Bypass Sidechain', 'Ratio'}
local function display(p, v)
  if p == 0 then
    local hz = 20 * 1000 ^ v
    if hz >= 1000 then return string.format('%.2f kHz', hz / 1000) end
    return string.format('%.1f Hz', hz)
  elseif p == 1 then
    if v == 0 then return '-inf dB' end
    return string.format('%+.1f dB', -30 + 60 * v)
  elseif p == 2 then
    return ({'Clean', 'Punch', 'Spectral'})[math.floor(v * 2 + 0.5) + 1]
  elseif p == 3 then
    return v >= 0.5 and 'On' or 'Off'
  else
    return string.format('%.2f:1', 1 + 19 * v)
  end
end
local track = {}
reaper = {
  EnumProjects = function() return 'project', 'song.rpp' end,
  CountTracks = function() return 1 end,
  GetTrack = function() return track end,
  GetTrackName = function() return true, 'Bass' end,
  GetTrackGUID = function() return '{AAAA0000-0000-0000-0000-000000000001}' end,
  GetMasterTrack = function() return {} end,
  TrackFX_GetCount = function() return 1 end,
  TrackFX_GetFXGUID = function() return '{BBBB0000-0000-0000-0000-000000000002}' end,
  TrackFX_GetFXName = function() return true, 'VST3: Test Comp (Vendor)' end,
  TrackFX_GetEnabled = function() return true end,
  TrackFX_GetOffline = function() return false end,
  TrackFX_GetNumParams = function() return #names end,
  TrackFX_GetParamName = function(_, _, p) return true, names[p + 1] end,
  TrackFX_GetParamNormalized = function(_, _, p) return values[p + 1] end,
  TrackFX_SetParamNormalized = function(_, _, p, v) values[p + 1] = v; return true end,
  TrackFX_GetFormattedParamValue = function(_, _, p) return true, display(p, values[p + 1]) end,
  TrackFX_FormatParamValueNormalized = function(_, _, p, v) return true, display(p, v) end,
  TrackFX_GetParameterStepSizes = function(_, _, p)
    if p == 2 then return true, 0.5, 0.5, 0.5, false end
    if p == 3 then return true, 1, 1, 1, true end
    return false
  end,
}
'''


@pytest.fixture()
def lua():
    runtime = lua_module.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(FAKE)
    runtime.globals().fx = runtime.execute((LUA / 'fx.lua').read_text(encoding='utf-8'))
    return runtime


def resolve(lua, param, target, normalized=False):
    lua.globals().target = target
    lua.globals().normalized = normalized
    value, note = lua.execute(f'local t = fx.track("Bass") return fx.resolve(t, fx.find(t, "test comp"), {param}, target, normalized)')
    shown = lua.eval(f'reaper.TrackFX_FormatParamValueNormalized(nil, 0, {param}, {value!r})')[1]
    return value, shown, note


def test_numeric_targets_reach_the_displayed_value(lua):
    assert resolve(lua, 0, '130 Hz')[1] == '130.0 Hz'
    assert resolve(lua, 0, '1.2 kHz')[1] == '1.20 kHz'
    assert resolve(lua, 0, '1200 Hz')[1] == '1.20 kHz'
    assert resolve(lua, 1, '-18 dB')[1] == '-18.0 dB'
    assert resolve(lua, 1, -18)[1] == '-18.0 dB'
    assert resolve(lua, 4, '4:1')[1] == '4.00:1'


def test_infinite_range_end(lua):
    lua.execute("local old = reaper.TrackFX_FormatParamValueNormalized "
                "reaper.TrackFX_FormatParamValueNormalized = function(t, f, p, v) "
                "if p == 4 then if v >= 1 then return true, 'inf :1' end return true, string.format('%.3f :1', 1 / (1 - v)) end "
                "return old(t, f, p, v) end")
    assert resolve(lua, 4, '4:1')[1] == '4.000 :1'


def test_list_and_switch_targets_match_text(lua):
    assert resolve(lua, 2, 'spectral')[1] == 'Spectral'
    assert resolve(lua, 2, 'Pun')[1] == 'Punch'
    assert resolve(lua, 3, 'On')[1] == 'On'


def test_out_of_range_and_wrong_targets(lua):
    value, shown, note = resolve(lua, 0, '50 kHz')
    assert shown == '20.00 kHz' and 'closest reachable' in note
    with pytest.raises(lua_module.LuaError, match='settings are: Clean | Punch | Spectral'):
        resolve(lua, 2, 'Vintage')
    with pytest.raises(lua_module.LuaError, match='Unit'):
        resolve(lua, 0, '-6 dB')
    assert resolve(lua, 1, 0.75, normalized=True)[1] == '+15.0 dB'


def test_parameter_rows_and_lookup(lua):
    row = lua.execute('local t = fx.track("{aaaa0000-0000-0000-0000-000000000001}") return fx.param_row(t, 0, 2, true)')
    assert row['shown'] == 'Clean' and list(row['choices'].values()) == ['Clean', 'Punch', 'Spectral']
    assert lua.execute('local t = fx.track("Bass") return fx.param_index(t, 0, "ratio")') == 4
    with pytest.raises(lua_module.LuaError, match='No parameter'):
        lua.execute('local t = fx.track("Bass") return fx.param_index(t, 0, "drive")')


def test_server_programs_compile():
    runtime = lua_module.LuaRuntime()
    compile_ = runtime.eval('function(src) local f, e = load(src) return e end')
    for body in (server.ADD_FX, server.FX_PARAMETERS, server.SET_FX_PARAMETERS, server.EDIT_FX, server.SIDECHAIN_SEND):
        assert compile_(server.fx_program(body, track='Bass', fx=0)) is None
