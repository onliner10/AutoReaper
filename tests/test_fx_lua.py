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
    value, note = lua.execute(f'local t = fx.track("Bass") return fx.resolve(t, fx.find(t, "test comp"), {param}, target, normalized)')[:2]
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


def set_param(lua, param, target, normalized=False):
    lua.globals().target = target
    lua.globals().normalized = normalized
    return lua.execute(f'local t = fx.track("Bass") return fx.set(t, 0, {param}, target, normalized)')


def test_set_reads_back_what_it_set(lua):
    value, note, after = set_param(lua, 1, '-18 dB')
    assert after == '-18.0 dB' and 'reading back' not in note


def test_set_searches_live_when_the_plugin_formats_its_current_value(lua):
    # Like some VST3s: formatting any position returns what is displayed now.
    lua.execute("reaper.TrackFX_FormatParamValueNormalized = function(t, f, p) "
                "return reaper.TrackFX_GetFormattedParamValue(t, f, p) end")
    assert set_param(lua, 1, '-18 dB')[2] == '-18.0 dB'
    value, note, after = set_param(lua, 2, 'Spectral')
    assert after == 'Spectral' and 'reading back' in note


def test_set_searches_live_when_the_plugin_formats_wrong_values(lua):
    # Like some CLAPs: formatting answers, but not with what the plugin then shows.
    lua.execute("reaper.TrackFX_FormatParamValueNormalized = function(t, f, p, v) "
                "return true, string.format('%+.1f dB', -30 + 60 * (1 - v)) end")
    value, note, after = set_param(lua, 1, '+12 dB')
    assert after == '+12.0 dB' and 'reading back' in note


def test_set_restores_the_parameter_when_the_target_is_unreachable(lua):
    lua.execute("reaper.TrackFX_FormatParamValueNormalized = function(t, f, p) "
                "return reaper.TrackFX_GetFormattedParamValue(t, f, p) end")
    with pytest.raises(lua_module.LuaError, match='Vintage'):
        set_param(lua, 2, 'Vintage')
    assert lua.eval('reaper.TrackFX_GetParamNormalized(nil, 0, 2)') == 0


def run_body(lua, body, **arguments):
    return lua.execute(server.fx_program(body, **arguments))


def test_set_fx_parameters_reports_changed_from_its_readback(lua):
    first = run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=[{'param': 'Gain', 'value': '-18 dB'}])
    assert first['changed'] is True and first['failed'] == 0
    again = run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=[{'param': 'Gain', 'value': '-18 dB'}])
    assert again['changed'] is False


def test_track_names_resolve_to_guids(lua):
    result = run_body(lua, server.RESOLVE_TRACKS, refs=['Bass'])
    assert result['guids'][1] == '{AAAA0000-0000-0000-0000-000000000001}'
    with pytest.raises(lua_module.LuaError, match='No track'):
        run_body(lua, server.RESOLVE_TRACKS, refs=['Kick'])


def test_server_programs_compile():
    runtime = lua_module.LuaRuntime()
    compile_ = runtime.eval('function(src) local f, e = load(src) return e end')
    for body in (server.ADD_FX, server.FX_PARAMETERS, server.SET_FX_PARAMETERS, server.EDIT_FX, server.SIDECHAIN_SEND,
                 server.RESOLVE_TRACKS, server.RESOLVE_BYPASS):
        assert compile_(server.fx_program(body, track='Bass', fx=0)) is None
    for body in (server.ADD_FAUST_FX, server.READ_FAUST_FX, server.EDIT_FAUST_FX):
        assert compile_(server.faust_program(body, track='Lead', fx=0)) is None


def test_set_does_not_probe_live_when_formatting_is_reliable(lua):
    lua.execute("sets = 0 local old = reaper.TrackFX_SetParamNormalized "
                "reaper.TrackFX_SetParamNormalized = function(...) sets = sets + 1 return old(...) end")
    with pytest.raises(lua_module.LuaError, match='settings are: Clean'):
        set_param(lua, 2, 'Vintage')
    assert lua.eval('sets') == 0


# Like Soundtoys and FabFilter CLAP plugins in REAPER 7.86 while the audio device is closed or the plugin is
# bypassed: a host change waits in the plugin's queue and lands (last one wins) on its next audio block.
QUEUED = '''
queue = {}
local set = reaper.TrackFX_SetParamNormalized
reaper.TrackFX_SetParamNormalized = function(t, f, p, v) queue[p] = v return true end
function process() for p, v in pairs(queue) do set(nil, 0, p, v) end queue = {} end
'''


def test_set_reports_a_queued_change_as_pending(lua):
    lua.execute(QUEUED)
    value, note, after, pending = set_param(lua, 1, '-18 dB')
    assert pending is True and after == '-18.0 dB'
    assert lua.eval('reaper.TrackFX_GetFormattedParamValue(nil, 0, 1)')[1] == '+0.0 dB'  # not yet
    lua.execute('process()')
    assert lua.eval('reaper.TrackFX_GetFormattedParamValue(nil, 0, 1)')[1] == '-18.0 dB'


def test_set_keeps_a_queued_normalized_value(lua):
    lua.execute(QUEUED)
    value, note, after, pending = set_param(lua, 1, 0.8, normalized=True)
    assert pending is True and value == 0.8 and after == '+18.0 dB'


def test_queued_change_without_reliable_formatting_is_refused_and_left(lua):
    lua.execute(QUEUED + "reaper.TrackFX_FormatParamValueNormalized = function(t, f, p) "
                "return reaper.TrackFX_GetFormattedParamValue(t, f, p) end")
    with pytest.raises(lua_module.LuaError, match='only while it processes audio.*normalized'):
        set_param(lua, 1, '-18 dB')
    lua.execute('process()')
    assert lua.eval('reaper.TrackFX_GetParamNormalized(nil, 0, 1)') == 0.5


def test_display_without_its_unit_matches_the_formatted_value(lua):
    # Soundtoys: formatting says "30.0 %", the control shows "30.0".
    same = lua.eval('fx.same_display')
    assert same('30.0', '30.0 %') and same(' 110.44 Hz', '110.44 Hz') and same('-inf dB', '-inf')
    assert not same('30.0', '30.1 %') and not same('12 dB/oct', '12 dB') and not same('Bell', 'Notch')
    assert not same('30 %', '30 Hz') and not same(None, '30')
    lua.execute("local old = reaper.TrackFX_GetFormattedParamValue "
                "reaper.TrackFX_GetFormattedParamValue = function(t, f, p) "
                "local ok, s = old(t, f, p) return ok, (s:gsub(' dB$', '')) end")
    value, note, after = set_param(lua, 1, '-18 dB')[:3]
    assert after == '-18.0' and 'reading back' not in note


GAIN = [{'param': 'Gain', 'value': '-18 dB'}]
# The server's flush_audio renders 50 ms of the track; here it just runs the plugin's queue.
FLUSH = "flushed = nil function flush_audio(guids, switches) flushed = {guids = guids, switches = switches} process() end "


def test_set_fx_parameters_applies_queued_changes_in_the_same_request(lua):
    lua.execute(QUEUED + FLUSH + "reaper.GetPlayStateEx = function() return 0 end")
    result = run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=GAIN)
    row = result['results'][1]
    assert result['failed'] == 0 and result['changed'] is True
    assert row['after'] == '-18.0 dB' and 'applied once' in row['note'] and 'pending' not in row and 'original' not in row
    assert lua.eval('flushed.guids[1]') == '{AAAA0000-0000-0000-0000-000000000001}' and lua.eval('#flushed.switches') == 0


def test_a_bypassed_plugin_is_switched_on_for_the_flush_only(lua):
    lua.execute(QUEUED + FLUSH + "reaper.GetPlayStateEx = function() return 0 end "
                "reaper.TrackFX_GetEnabled = function() return false end")
    assert run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=GAIN)['failed'] == 0
    assert lua.eval('flushed.switches[1].enabled') is True
    assert lua.eval('flushed.switches[1].fx') == '{BBBB0000-0000-0000-0000-000000000002}'


def test_queued_changes_stay_queued_while_playing_or_when_the_flush_fails(lua):
    lua.execute(QUEUED + FLUSH + "reaper.GetPlayStateEx = function() return 1 end")
    row = run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=GAIN)['results'][1]
    assert row['pending'] is True and row['unverified'] == 'REAPER is playing, paused or recording' and lua.eval('flushed') is None
    lua.execute("queue = {} reaper.GetPlayStateEx = function() return 0 end "
                "function flush_audio() error('[string \"x\"]:9: Choose Full-speed Offline once\\nstack', 0) end")
    row = run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=GAIN)['results'][1]
    assert row['pending'] is True and row['unverified'] == 'Choose Full-speed Offline once'


def test_a_change_the_plugin_never_applies_fails_after_the_flush(lua):
    lua.execute(QUEUED + FLUSH + "reaper.GetPlayStateEx = function() return 0 end process = function() queue = {} end")
    result = run_body(lua, server.SET_FX_PARAMETERS, track='Bass', fx=0, changes=GAIN)
    assert result['failed'] == 1 and 'did not apply' in result['results'][1]['error']
    assert lua.eval('queue[1]') == 0.5  # the original is what lands later, not the failed value


def test_queued_master_plugin_is_flushed_through_the_full_mix(lua):
    lua.execute(QUEUED + FLUSH + "reaper.GetPlayStateEx = function() return 0 end "
                "local master = {} reaper.GetMasterTrack = function() return master end")
    assert run_body(lua, server.SET_FX_PARAMETERS, track='master', fx=0, changes=GAIN)['failed'] == 0
    assert lua.eval('#flushed.guids') == 0  # the master cannot be soloed


def test_words_around_the_number_must_match(lua):
    same = lua.eval('fx.same_display')
    assert not same('LP 12', 'HP 12') and not same('Band 1', 'Mode 1') and same('Band 1', 'band 1')


def test_a_rounding_plugin_is_not_taken_for_a_queue(lua):
    # Steps of 5 dB from +0 dB: "+2 dB" rounds back to +0 dB; the readback does move for other steps.
    lua.execute("local old = reaper.TrackFX_SetParamNormalized "
                "reaper.TrackFX_SetParamNormalized = function(t, f, p, v) "
                "if p == 1 then v = math.floor(v * 12 + 0.5) / 12 end return old(t, f, p, v) end")
    result = set_param(lua, 1, '+2 dB')  # value, note, after: no fourth "pending"
    assert len(result) == 3 and result[2] in ('+0.0 dB', '+5.0 dB')
    result = set_param(lua, 1, 0.52, normalized=True)
    assert len(result) == 3 and result[2] == '+0.0 dB'


def test_set_accepts_a_plugin_that_quantizes(lua):
    # Gain stored in steps of 1/12 (5 dB): the searched position snaps, the live search finds the step.
    lua.execute("local old = reaper.TrackFX_SetParamNormalized "
                "reaper.TrackFX_SetParamNormalized = function(t, f, p, v) "
                "if p == 1 then v = math.floor(v * 12 + 0.5) / 12 end return old(t, f, p, v) end")
    value, note, after = set_param(lua, 1, '+5 dB')
    assert after == '+5.0 dB'
    assert set_param(lua, 1, '+5 dB')[2] == '+5.0 dB'  # already there: no false "did not take"


def test_note_value_lists_are_matched_as_text(lua):
    lua.execute("local old = reaper.TrackFX_FormatParamValueNormalized "
                "local notes = {'1/64', '1/32', '1/16', '1/8', '1/4'} "
                "reaper.TrackFX_FormatParamValueNormalized = function(t, f, p, v) "
                "if p == 4 then return true, notes[math.floor(v * 4 + 0.5) + 1] end return old(t, f, p, v) end")
    assert resolve(lua, 4, '1/8')[1] == '1/8'
    with pytest.raises(lua_module.LuaError, match='settings are: 1/64 | 1/32'):
        resolve(lua, 4, '1/8.')


def test_faust_state_round_trip_matches_the_plugin_format():
    # The state as plugin/src/engine.cpp writes it: sized fields, code with quotes and new lines.
    code = 'import("stdfaust.lib");\nprocess = _ : *(0.5); // "half"\n'
    raw = ('AutoReaperFaust 1\nstatus error\ninputs 4\noutputs 2\nmessages 13\nfaust : 2 : x\n'
           f'code {len(code)}\n{code}\ndraft 13\nprocess = _;\n\n')
    runtime = lua_module.LuaRuntime(unpack_returned_tuples=True)
    faust = runtime.execute((LUA / 'faust.lua').read_text(encoding='utf-8'))
    state = faust.parse(faust.decode(faust.encode(raw)))
    assert (state.code, state.draft, state.messages, state.status, state.inputs) == (
        code, 'process = _;\n', 'faust : 2 : x', 'error', 4)
    written = faust.parse(faust.serialize(code))
    assert written.code == code and written.draft == '' and written.status == 'none'
    assert faust.hash(code) == faust.hash(code) != faust.hash(code + ' ')
    for size in range(5):  # base64 padding
        assert faust.decode(faust.encode('abcd'[:size])) == 'abcd'[:size]


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_faust_state_in_a_track_chunk(newline):
    """faust.read/write find the plugin's <STATE> by FX GUID, with Unix or Windows line ends."""
    runtime = lua_module.LuaRuntime(unpack_returned_tuples=True)
    faust = runtime.execute((LUA / 'faust.lua').read_text(encoding='utf-8'))
    code = 'process = _ * 0.5;\n'
    encoded = faust.encode(faust.serialize(code))
    lines = ['<TRACK', 'NAME LEAD', '<FXCHAIN', 'BYPASS 0 0 0', '<VST "VSTi: Other" other.so', 'AAAA', '>',
             'FXID {11111111-0000-0000-0000-000000000000}',
             '<CLAP "CLAP: Faust (AutoReaper)" com.autoreaper.faust ""', 'CFG 4 0 0 ""', '<STATE',
             encoded[:40], encoded[40:], '>', '>', 'FXID {22222222-0000-0000-0000-000000000000}', '>', '>']
    runtime.globals().chunk = newline.join(lines) + newline
    runtime.execute('''
      reaper = {
        GetTrackStateChunk = function() return true, chunk end,
        SetTrackStateChunk = function(_, c) chunk = c; return true end,
        TrackFX_GetFXGUID = function() return '{22222222-0000-0000-0000-000000000000}' end,
        TrackFX_GetNamedConfigParm = function() return true, '/x/AutoReaper Faust.clap<com.autoreaper.faust' end,
      }''')
    state = faust.read(None, 1)
    assert state.code == code and state.status == 'none'
    after = faust.write(None, 1, faust.serialize('process = _;\n'))
    assert after.code == 'process = _;\n'
    assert 'NAME LEAD' in runtime.globals().chunk and 'AAAA' in runtime.globals().chunk  # the rest untouched


@pytest.mark.parametrize('offline', [True, False])
def test_faust_effect_known_when_not_running(offline):
    """An offline or missing plugin has no fx_ident; its chunk line still names it, and not_running says why."""
    runtime = lua_module.LuaRuntime(unpack_returned_tuples=True)
    faust = runtime.execute((LUA / 'faust.lua').read_text(encoding='utf-8'))
    lines = ['<TRACK', '<FXCHAIN', '<VST "VST: Other" other.so', 'AAAA', '>',
             'FXID {11111111-0000-0000-0000-000000000000}',
             '<CLAP "CLAP: Faust (AutoReaper)" com.autoreaper.faust Ducker', 'CFG 4 0 0 ""', '<STATE', 'QUFB', '>', '>',
             'FXID {22222222-0000-0000-0000-000000000000}', '>', '>']
    runtime.globals().chunk = '\n'.join(lines) + '\n'
    runtime.globals().offline = offline
    runtime.execute('''
      local guids = {[0] = '{11111111-0000-0000-0000-000000000000}', [1] = '{22222222-0000-0000-0000-000000000000}'}
      reaper = {
        GetTrackStateChunk = function() return true, chunk end,
        TrackFX_GetFXGUID = function(_, i) return guids[i] end,
        TrackFX_GetNamedConfigParm = function() return true, '' end,
        TrackFX_GetOffline = function() return offline end,
      }''')
    assert faust.is_faust(None, 1) and not faust.is_faust(None, 0)
    assert ('offline' if offline else 'not installed') in faust.not_running(None, 1)


# Two tracks; widening the bus moves the key pins of the compressor already on it, as REAPER can.
WIDENING = r'''
local tracks = {{name = 'Kick', nchan = 2}, {name = 'Bus', nchan = 4}}
local pins = {[0] = 1, [1] = 2, [2] = 4, [3] = 8}
local sends = {}
reaper = {
  EnumProjects = function() return 'project', 'song.rpp' end,
  CountTracks = function() return #tracks end,
  GetTrack = function(_, i) return tracks[i + 1] end,
  GetMasterTrack = function() return {} end,
  GetTrackName = function(t) return true, t.name end,
  GetTrackGUID = function(t) return '{' .. t.name .. '}' end,
  GetTrackNumSends = function() return #sends end,
  GetTrackSendInfo_Value = function(_, _, s, key) return sends[s + 1][key] or 0 end,
  SetTrackSendInfo_Value = function(_, _, s, key, v) sends[s + 1][key] = v; return true end,
  CreateTrackSend = function(_, dest) sends[#sends + 1] = {P_DESTTRACK = dest}; return #sends - 1 end,
  GetMediaTrackInfo_Value = function(t, key) return key == 'I_NCHAN' and t.nchan or 0 end,
  SetMediaTrackInfo_Value = function(t, key, v)
    if key == 'I_NCHAN' then t.nchan = v; pins[2], pins[3] = 1 << 4, 1 << 5 end
    return true
  end,
  TrackFX_GetCount = function(t) return t.name == 'Bus' and 1 or 0 end,
  TrackFX_GetFXName = function() return true, 'VST3: Pro-C 3 (FabFilter)' end,
  TrackFX_GetFXGUID = function() return '{FX}' end,
  TrackFX_GetIOSize = function() return true, 4, 2 end,
  TrackFX_GetNamedConfigParm = function() return true, '' end,
  TrackFX_GetPinMappings = function(_, _, _, pin) return pins[pin], 0 end,
}
'''


def test_sidechain_send_reports_other_plugins_whose_pins_moved():
    runtime = lua_module.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(WIDENING)
    result = runtime.execute(server.fx_program(server.SIDECHAIN_SEND, source='Kick', target='Bus', kind='audio',
                                               channels=7, midi_bus=1, fx=None))
    moved = result['other_fx_pins_moved']
    assert moved[1]['pins_before'] == '1 2 3 4' and moved[1]['pins_after'] == '1 2 5 6'
    assert 'warning' in result and result['target_track_channels'] == 8


# Two tracks on a 120 BPM 4/4 grid (2 s per bar): one with the user's automation, one without a lane.
ENVELOPES = r'''
local function env(points) return {points = points} end
local tracks = {{name = 'Lead', env = env({{0, 1, 0, 0}, {10, 0.5, 0, 0}})}, {name = 'Pad'}}
local ext = {}
reaper = {
  CountTracks = function() return #tracks end,
  GetTrack = function(_, i) return tracks[i + 1] end,
  GetTrackName = function(t) return true, t.name end,
  GetTrackGUID = function(t) return '{' .. t.name .. '}' end,
  TimeMap2_beatsToTime = function(_, _, measure) return measure * 2 end,
  TimeMap2_timeToQN = function(_, t) return t * 2 end,
  TimeMap2_QNToTime = function(_, qn) return qn / 2 end,
  GetTrackEnvelopeByName = function(t) return t.env end,
  GetTrackStateChunk = function() return true, '<TRACK\n>' end,
  SetTrackStateChunk = function(t) t.env = env({}); t.env.points = {{0, 1, 0, 0}}; return true end,
  genGuid = function() return '{E}' end,
  GetEnvelopeScalingMode = function() return 0 end,
  ScaleFromEnvelopeMode = function(_, v) return v end,
  ScaleToEnvelopeMode = function(_, v) return v end,
  GetExtState = function(s, k) return ext[s .. k] or '' end,
  SetExtState = function(s, k, v) ext[s .. k] = v end,
  CountEnvelopePoints = function(e) return #e.points end,
  GetEnvelopePoint = function(e, i) local p = e.points[i + 1]; return true, p[1], p[2], p[3], p[4] end,
  DeleteEnvelopePointRange = function(e) e.points = {} end,
  InsertEnvelopePoint = function(e, t, v, shape, tension) e.points[#e.points + 1] = {t, v, shape, tension}; return true end,
  Envelope_SortPoints = function(e) table.sort(e.points, function(a, b) return a[1] < b[1] end) end,
  CountTrackEnvelopes = function(t) return t.env and 1 or 0 end,
  GetTrackEnvelope = function(t) return t.env end,
  GetEnvelopeName = function() return true, 'Volume' end,
}
function value_at(name, time)
  for _, t in ipairs(tracks) do
    if t.name == name then
      for _, p in ipairs(t.env.points) do if math.abs(p[1] - time) < 1e-9 then return p[2] end end
    end
  end
end
'''


def test_volume_offsets_keep_the_users_points_and_rerun_from_them():
    from pathlib import Path
    script = (Path(server.__file__).parents[2] / 'skills' / 'reaper' / 'scripts' / 'volume_offsets.lua')
    runtime = lua_module.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(ENVELOPES)
    apply = runtime.execute(script.read_text(encoding='utf-8'))
    spec = lambda db: runtime.eval('function(db) return {key = "t", groups = {{tracks = {"Lead", "Pad"}, '
                                   'offsets = {{1, db}, {3, 0}}}}} end')(db)
    report = apply(spec(-6.0))
    assert 'new envelope' in report[2] and 'volume lanes 1' in report[1]
    half = 10 ** (-6 / 20)
    # Bar 1 starts at 0 s: the user's unity point now sits 6 dB lower; bar 3 (4 s) is back to the user's curve.
    assert runtime.globals().value_at('Lead', 0) == pytest.approx(half)
    assert runtime.globals().value_at('Lead', 4) == pytest.approx(1 - 0.5 * 4 / 10)
    assert runtime.globals().value_at('Lead', 10) == pytest.approx(0.5)
    apply(spec(-3.0))  # a re-run starts from the stored originals, not from the -6 dB result
    assert runtime.globals().value_at('Lead', 0) == pytest.approx(10 ** (-3 / 20))
    assert runtime.globals().value_at('Pad', 0) == pytest.approx(10 ** (-3 / 20))
