-- Plugin helpers shared by the FX tools. The server prepends this file to a
-- short call, so everything here is local and returned in one table.
local fx = {}
local project = reaper.EnumProjects(-1, '')

local function guid_key(value) return (tostring(value or ''):gsub('[{}]', ''):upper()) end

-- A track by GUID (with or without braces), "master", or an exact unique name.
function fx.track(ref)
  assert(type(ref) == 'string' and ref ~= '', 'track is required: a track GUID, "master" or an exact track name')
  if ref:lower() == 'master' then return reaper.GetMasterTrack(project), 'MASTER' end
  local key, named = guid_key(ref), {}
  for i = 0, reaper.CountTracks(project) - 1 do
    local track = reaper.GetTrack(project, i)
    local _, name = reaper.GetTrackName(track)
    if guid_key(reaper.GetTrackGUID(track)) == key then return track, name end
    if name == ref then named[#named + 1] = track end
  end
  assert(#named < 2, 'More than one track is named "' .. ref .. '"; use its GUID')
  assert(#named == 1, 'No track ' .. ref .. ' in the active project')
  return named[1], ref
end

-- An FX on a track by GUID, zero-based index, or a unique part of its name.
function fx.find(track, ref)
  local count = reaper.TrackFX_GetCount(track)
  if type(ref) == 'number' then
    assert(ref >= 0 and ref < count and ref % 1 == 0, 'FX index ' .. ref .. ' is out of range (0..' .. (count - 1) .. ')')
    return ref
  end
  assert(type(ref) == 'string' and ref ~= '', 'fx is required: an FX GUID, index or part of its name')
  local key, lowered, hits = guid_key(ref), ref:lower(), {}
  for i = 0, count - 1 do
    if guid_key(reaper.TrackFX_GetFXGUID(track, i)) == key then return i end
    local _, name = reaper.TrackFX_GetFXName(track, i)
    if name:lower():find(lowered, 1, true) then hits[#hits + 1] = i end
  end
  assert(#hits < 2, 'More than one FX matches "' .. ref .. '"; use its GUID or index')
  assert(#hits == 1, 'No FX matching ' .. ref .. ' on this track')
  return hits[1]
end

function fx.describe(track, index)
  local _, name = reaper.TrackFX_GetFXName(track, index)
  return {index = index, guid = reaper.TrackFX_GetFXGUID(track, index), name = name,
          enabled = reaper.TrackFX_GetEnabled(track, index), offline = reaper.TrackFX_GetOffline(track, index),
          parameter_count = reaper.TrackFX_GetNumParams(track, index)}
end

function fx.chain(track)
  local rows = {}
  for i = 0, reaper.TrackFX_GetCount(track) - 1 do rows[#rows + 1] = fx.describe(track, i) end
  return rows
end

-- What the plugin would display for a normalized value, without changing it.
-- nil when the plugin does not support formatting arbitrary values.
local function format_at(track, index, param, value)
  local ok, text = reaper.TrackFX_FormatParamValueNormalized(track, index, param, value, '')
  if ok and text ~= '' then return text end
  return nil
end

local function shown(track, index, param)
  local _, text = reaper.TrackFX_GetFormattedParamValue(track, index, param, '')
  return text
end

-- How a parameter displays at a normalized value. The default asks the plugin
-- to format the value without setting it. Some plugins answer that with their
-- current display (or a wrong one), so the live probe sets the parameter and
-- reads it back instead; it leaves the parameter at the last probed value.
local function formatter(track, index, param)
  return function(value) return format_at(track, index, param, value) end
end

function fx.live_probe(track, index, param)
  return function(value)
    reaper.TrackFX_SetParamNormalized(track, index, param, value)
    local text = shown(track, index, param)
    if text == '' then return nil end
    return text
  end
end

-- Number and unit family of a displayed value: "1.2 kHz" -> 1200, "hz";
-- "0.5 s" -> 500, "ms"; "-inf dB" -> -1e300, "db". nil for plain text.
function fx.quantity(text)
  if type(text) ~= 'string' then return nil end
  local lowered = text:lower():gsub(',', '.')
  local unit_hint = lowered:find('db') and 'db' or (lowered:find(':') and 'ratio' or '')
  if lowered:find('%-inf') or lowered:find('−inf') then return -1e300, unit_hint end
  if lowered:find('^%s*%+?inf') then return 1e300, unit_hint end
  local number, rest = lowered:match('([-+]?%d*%.?%d+)%s*(.*)$')
  if not number then return nil end
  local value, unit = tonumber(number), rest:gsub('^%s+', ''):gsub('%s+$', '')
  if not value then return nil end
  if unit:find('^khz') or unit == 'k' then return value * 1000, 'hz' end
  if unit:find('^hz') then return value, 'hz' end
  if unit:find('^ms') then return value, 'ms' end
  if unit == 's' or unit:find('^sec') then return value * 1000, 'ms' end
  if unit:find('^db') then return value, 'db' end
  if unit:find('^%%') then return value, '%' end
  if unit:find('^:') then return value, 'ratio' end
  return value, unit
end

-- Discrete settings of a parameter: {{value=normalized, shown=text}, ...}, or nil
-- for a continuous one. Uses the plugin's step size, else probes 201 points.
function fx.choices(track, index, param, limit, probe)
  limit = limit or 64
  probe = probe or formatter(track, index, param)
  local ok, step, _, _, toggle = reaper.TrackFX_GetParameterStepSizes(track, index, param)
  local positions = {}
  if ok and toggle then
    positions = {0, 1}
  elseif ok and step and step > 0 and 1 / step <= 512 then
    local n = math.floor(1 / step + 0.5)
    for i = 0, n do positions[#positions + 1] = math.min(1, i * step) end
  else
    return nil
  end
  local rows, last = {}, nil
  for _, value in ipairs(positions) do
    local text = probe(value)
    if not text then return nil end
    if text ~= last then rows[#rows + 1] = {value = value, shown = text}; last = text end
    if #rows > limit then return nil end
  end
  return rows
end

-- Text settings found by probing: runs of identical displayed text across 0..1.
local function probed_runs(probe)
  local runs = {}
  for i = 0, 200 do
    local value = i / 200
    local text = probe(value)
    if not text then return nil end
    local run = runs[#runs]
    if run and run.shown == text then run.last = value else runs[#runs + 1] = {shown = text, first = value, last = value} end
  end
  return runs
end

function fx.param_index(track, index, ref)
  local count = reaper.TrackFX_GetNumParams(track, index)
  if type(ref) == 'number' then
    assert(ref >= 0 and ref < count and ref % 1 == 0, 'Parameter index ' .. ref .. ' is out of range (0..' .. (count - 1) .. ')')
    return ref
  end
  assert(type(ref) == 'string' and ref ~= '', 'param is required: an index or a parameter name')
  local lowered, exact, partial = ref:lower(), {}, {}
  for p = 0, count - 1 do
    local _, name = reaper.TrackFX_GetParamName(track, index, p, '')
    if name:lower() == lowered then exact[#exact + 1] = p
    elseif name:lower():find(lowered, 1, true) then partial[#partial + 1] = p end
  end
  local hits = #exact > 0 and exact or partial
  assert(#hits < 2, 'More than one parameter matches "' .. ref .. '"; use its index (fx_parameters lists them)')
  assert(#hits == 1, 'No parameter matching "' .. ref .. '"')
  return hits[1]
end

function fx.param_row(track, index, param, with_choices)
  local _, name = reaper.TrackFX_GetParamName(track, index, param, '')
  local row = {index = param, name = name, value = math.floor(reaper.TrackFX_GetParamNormalized(track, index, param) * 1e5 + 0.5) / 1e5,
               shown = shown(track, index, param)}
  local low, high = format_at(track, index, param, 0), format_at(track, index, param, 1)
  if low and high and low ~= high then row.range = {low, high} end
  if with_choices then
    local choices = fx.choices(track, index, param, 32)
    if choices and #choices <= 32 then
      local names = {}
      for _, c in ipairs(choices) do names[#names + 1] = c.shown end
      row.choices = names
    end
  end
  return row
end

local function closer(a, b, target) return math.abs(a - target) <= math.abs(b - target) end

-- The normalized value that makes the plugin display `target` ("130 Hz",
-- "-18 dB", "Spectral", "4:1"), or a number 0..1 when normalized is true.
-- Returns value, note, and what the plugin should display at value. Errors
-- when the target cannot be reached. probe defaults to formatting without
-- setting; pass fx.live_probe(...) for plugins whose formatting is unreliable.
function fx.resolve(track, index, param, target, normalized, probe)
  if normalized then
    assert(type(target) == 'number' and target >= 0 and target <= 1, 'A normalized value must be a number 0..1')
    return target, 'normalized'
  end
  probe = probe or formatter(track, index, param)
  local text = tostring(target)
  local low_text, high_text = probe(0), probe(1)
  assert(low_text, 'This plugin does not report display values for unset positions; pass a normalized value (0..1) and check the readback')
  -- Discrete settings: match the displayed text.
  local runs = fx.choices(track, index, param, 512, probe)
  if not runs then
    local probed = probed_runs(probe)
    if probed and #probed <= 64 then
      runs = {}
      for _, r in ipairs(probed) do runs[#runs + 1] = {value = (r.first + r.last) / 2, shown = r.shown} end
    end
  end
  local lowered = text:lower()
  if runs then
    local exact, prefix = nil, {}
    for _, r in ipairs(runs) do
      local s = r.shown:lower():gsub('^%s+', ''):gsub('%s+$', '')
      if s == lowered then exact = r end
      if s:find(lowered, 1, true) == 1 then prefix[#prefix + 1] = r end
    end
    if exact then return exact.value, 'matched "' .. exact.shown .. '"', exact.shown end
    if #prefix == 1 then return prefix[1].value, 'matched "' .. prefix[1].shown .. '"', prefix[1].shown end
  end
  -- Numeric: binary search on the displayed quantity.
  local want, want_unit = fx.quantity(text)
  local low, low_unit = fx.quantity(low_text)
  local high, high_unit = fx.quantity(high_text)
  -- Settings that are not quantities ("1/16", "Lyra") can only be matched as text.
  local listed = false
  for _, r in ipairs(runs or {}) do
    local q, unit = fx.quantity(r.shown)
    if not q or not ({hz = true, ms = true, db = true, ['%'] = true, ratio = true, [''] = true})[unit] then listed = true end
  end
  if listed or not (want and low and high and low ~= high) then
    local names = {}
    for _, r in ipairs(runs or {}) do names[#names + 1] = r.shown end
    error('Cannot reach "' .. text .. '"' .. (#names > 0 and ('; settings are: ' .. table.concat(names, ' | ')) or
      ('; display range is ' .. low_text .. ' .. ' .. high_text)), 0)
  end
  if want_unit ~= '' and low_unit ~= '' and want_unit ~= low_unit and want_unit ~= high_unit then
    error('Unit of "' .. text .. '" does not match the parameter (' .. low_text .. ' .. ' .. high_text .. ')', 0)
  end
  local increasing = high > low
  local lo, hi = 0, 1
  local best, best_q = 0, low
  if closer(high, low, want) then best, best_q = 1, high end
  for _ = 1, 48 do
    local mid = (lo + hi) / 2
    local q = fx.quantity(probe(mid) or '')
    if not q then break end
    if closer(q, best_q, want) then best, best_q = mid, q end
    if (q < want) == increasing then lo = mid else hi = mid end
  end
  local expected = probe(best)
  local note = 'displays ' .. (expected or '?')
  local span = math.abs(high - low)
  if math.abs(best_q - want) > math.max(1e-6, span * 0.02) then
    note = note .. ' (closest reachable to ' .. text .. '; range ' .. low_text .. ' .. ' .. high_text .. ')'
  end
  return best, note, expected
end

-- Set a parameter to a display value (or a normalized one) and read it back.
-- If the plugin then shows something other than what its formatting promised,
-- or its formatting cannot reach the target at all, search again by setting
-- and reading back. Returns value, note, after; errors (with the parameter
-- restored) when the target cannot be reached either way.
-- Whether formatting unset values matches what the plugin shows: it must
-- format the current value as displayed, and not show one text everywhere.
local function formatting_reliable(track, index, param, original)
  local now = shown(track, index, param)
  if format_at(track, index, param, original) ~= now then return false end
  local low, high = format_at(track, index, param, 0), format_at(track, index, param, 1)
  return not (low == now and high == now)
end

-- Why a plugin may ignore host changes, for error messages.
function fx.audio_hint()
  if reaper.Audio_IsRunning and reaper.Audio_IsRunning() == 0 then
    return " REAPER's audio engine is stopped, and some plugins apply host changes to parameters or pin " ..
      'layouts only while it runs: ask the user to start audio and retry, or to set it in the plugin window.'
  end
  return ' Some plugins ignore host changes to some parameters; ask the user to set it in the plugin window.'
end

function fx.set(track, index, param, target, normalized)
  local original = reaper.TrackFX_GetParamNormalized(track, index, param)
  local reliable = normalized or formatting_reliable(track, index, param, original)
  local ok, value, note, expected = pcall(fx.resolve, track, index, param, target, normalized)
  if not ok and reliable then error(value, 0) end
  local after
  if ok then
    reaper.TrackFX_SetParamNormalized(track, index, param, value)
    after = shown(track, index, param)
    if normalized or after == expected then return value, note, after end
  end
  local live_ok, live_value, live_note, live_expected =
    pcall(fx.resolve, track, index, param, target, false, fx.live_probe(track, index, param))
  if not live_ok then
    -- Searching found nothing: tell a plugin that ignores every change apart from an unreachable target.
    local ignored = true
    for _, probe_value in ipairs({0, 1}) do
      reaper.TrackFX_SetParamNormalized(track, index, param, probe_value)
      if math.abs(reaper.TrackFX_GetParamNormalized(track, index, param) - original) > 1e-9 then ignored = false end
    end
    reaper.TrackFX_SetParamNormalized(track, index, param, original)
    if ignored then error('The plugin did not take new values for this parameter.' .. fx.audio_hint(), 0) end
    error(live_value, 0)
  end
  reaper.TrackFX_SetParamNormalized(track, index, param, live_value)
  after = shown(track, index, param)
  if live_expected and after ~= live_expected then
    reaper.TrackFX_SetParamNormalized(track, index, param, original)
    error('The plugin did not keep "' .. tostring(target) .. '" (it shows ' .. after .. '); parameter restored', 0)
  end
  return live_value, live_note .. ' (found by setting and reading back: the plugin does not format unset values reliably)', after
end

-- TrackFX_SetPinMappings does not mark the project changed, so REAPER adds no
-- Undo point for it (verified in REAPER 7.81). Switching the FX off and on in
-- the same call does, and that Undo point restores the pins as well.
function fx.mark_changed(track, index)
  local enabled = reaper.TrackFX_GetEnabled(track, index)
  reaper.TrackFX_SetEnabled(track, index, not enabled)
  reaper.TrackFX_SetEnabled(track, index, enabled)
end

-- Input pins: names and the channels each one reads (1-based, up to 64).
function fx.pins(track, index)
  local _, inputs = reaper.TrackFX_GetIOSize(track, index)
  local rows = {}
  for pin = 0, math.min(inputs or 0, 16) - 1 do
    local _, name = reaper.TrackFX_GetNamedConfigParm(track, index, 'in_pin_' .. pin)
    local low, high = reaper.TrackFX_GetPinMappings(track, index, 0, pin)
    local channels = {}
    for bit = 0, 31 do if (low >> bit) & 1 == 1 then channels[#channels + 1] = bit + 1 end end
    for bit = 0, 31 do if (high >> bit) & 1 == 1 then channels[#channels + 1] = bit + 33 end end
    rows[#rows + 1] = {pin = pin + 1, name = name ~= '' and name or nil, channels = channels}
  end
  return rows
end

return fx
