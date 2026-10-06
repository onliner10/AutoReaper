-- Faust effects: the "Faust (AutoReaper)" CLAP plugin keeps its Faust code in
-- its own state, which REAPER stores in the project like any plugin setting.
-- These helpers read and replace that state in the track's chunk; the plugin
-- compiles whatever state it is given and reports the result in its state.
-- The server prepends this file (after fx.lua) to its Faust requests.
local faust = {}
faust.PLUGIN = 'CLAP:Faust (AutoReaper)'
faust.ID = 'com.autoreaper.faust'

-- FNV-1a, 32 bit: the version of a piece of code, for edits that must not
-- overwrite changes the user made in the meantime.
function faust.hash(text)
  local h = 2166136261
  for i = 1, #text do h = ((h ~ text:byte(i)) * 16777619) & 0xffffffff end
  return string.format('%08x', h)
end

local B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

function faust.encode(data)
  local out = {}
  for i = 1, #data, 3 do
    local a, b, c = data:byte(i, i + 2)
    local n = (a << 16) | ((b or 0) << 8) | (c or 0)
    local function digit(shift) return B64:sub(((n >> shift) & 63) + 1, ((n >> shift) & 63) + 1) end
    out[#out + 1] = digit(18) .. digit(12) .. (b and digit(6) or '=') .. (c and digit(0) or '=')
  end
  return table.concat(out)
end

function faust.decode(text)
  local values, out = {}, {}
  for i = 1, 64 do values[B64:byte(i)] = i - 1 end
  text = text:gsub('[^%w%+/=]', '')
  for i = 1, #text - 3, 4 do
    local a, b, c, d = text:byte(i, i + 3)
    local n = (values[a] << 18) | (values[b] << 12) | ((values[c] or 0) << 6) | (values[d] or 0)
    out[#out + 1] = string.char(n >> 16) .. (c ~= 61 and string.char((n >> 8) & 255) or '') ..
      (d ~= 61 and string.char(n & 255) or '')
  end
  return table.concat(out)
end

-- The plugin's state format (plugin/src/engine.cpp): header lines, and
-- fields with a byte count so code needs no escaping.
function faust.parse(data)
  if data:sub(1, 18) ~= 'AutoReaperFaust 1\n' then return nil end
  local state, at = {code = '', draft = '', messages = '', status = 'none', inputs = 0, outputs = 0}, 19
  while at <= #data do
    local line_end = data:find('\n', at, true)
    if not line_end then break end
    local key, value = data:sub(at, line_end - 1):match('^(%S+) (.*)$')
    at = line_end + 1
    if key == 'code' or key == 'draft' or key == 'messages' then
      local size = tonumber(value)
      state[key] = data:sub(at, at + size - 1)
      at = at + size + 1
    elseif key == 'inputs' or key == 'outputs' then
      state[key] = tonumber(value)
    elseif key then
      state[key] = value
    end
  end
  return state
end

function faust.serialize(code)
  return 'AutoReaperFaust 1\nstatus none\ninputs 0\noutputs 0\nmessages 0\n\ncode ' .. #code .. '\n' .. code .. '\n'
end

-- The <STATE> block of one plugin in a track chunk, found by its FX GUID:
-- returns the chunk and the start and end of the block's base64 lines.
local function state_span(track, index)
  local _, chunk = reaper.GetTrackStateChunk(track, '', false)
  local guid = reaper.TrackFX_GetFXGUID(track, index)
  local fxid = assert(chunk:find('FXID ' .. guid, 1, true), 'FX ' .. guid .. ' is not in the track chunk')
  local start, at = nil, 1
  while true do
    local found = chunk:find('<CLAP ', at, true)
    if not found or found > fxid then break end
    start, at = found, found + 1
  end
  assert(start, 'This FX is not a CLAP plugin')
  local first, last = chunk:find('<STATE\n', start, true)
  assert(first and first < fxid, 'The plugin has no state in the track chunk')
  local close = chunk:find('\n%s*>', last)
  return chunk, last + 1, close - 1
end

function faust.is_faust(track, index)
  local _, ident = reaper.TrackFX_GetNamedConfigParm(track, index, 'fx_ident')
  return ident ~= nil and ident:find(faust.ID, 1, true) ~= nil
end

-- The plugin's state, as it reports it now (REAPER asks it while building the chunk).
function faust.read(track, index)
  assert(faust.is_faust(track, index), 'This FX is not the Faust (AutoReaper) plugin')
  local chunk, first, last = state_span(track, index)
  local raw = faust.decode(chunk:sub(first, last))
  local state = assert(faust.parse(raw), 'The plugin state is not in a format this AutoReaper version reads')
  state.version = faust.hash(state.code)
  state.raw = raw
  return state
end

-- Give the plugin new state (it compiles the code at once) and read it back.
function faust.write(track, index, raw)
  local chunk, first, last = state_span(track, index)
  local encoded, lines = faust.encode(raw), {}
  for i = 1, #encoded, 128 do lines[#lines + 1] = encoded:sub(i, i + 127) end
  reaper.SetTrackStateChunk(track, chunk:sub(1, first - 1) .. table.concat(lines, '\n') .. chunk:sub(last + 1), false)
  return faust.read(track, index)
end

-- What the tools report about a Faust effect.
function faust.report(track, index, state)
  return {fx = fx.describe(track, index), code = state.code, version = state.version, status = state.status,
          messages = state.messages ~= '' and state.messages or nil, inputs = state.inputs, outputs = state.outputs,
          draft = state.draft ~= '' and state.draft or nil}
end

return faust
