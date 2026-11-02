-- Multiply section gain offsets into each track's ONE Volume envelope, keeping the user's own points.
-- Run it with reaper_eval_write (one Undo step):
--
--   local f = assert(io.open([[<skill dir>/scripts/volume_offsets.lua]], 'r'))
--   local apply = assert(load(f:read('a')))(); f:close()
--   return apply{
--     key = 'song_sections',                  -- ExtState section that keeps the original points
--     ramp_beats = 1,                         -- ramp into each section start
--     groups = {
--       {tracks = {'Kick', '{GUID...}'},      -- exact names or GUIDs
--        offsets = {{1, -5.5}, {49, 0}, {57, -3.5}, {240, -6, 236}}},  -- {bar, dB [, ramp from bar]}
--     },
--   }
--
-- The first run stores every touched track's original points in the session ExtState `key`, so running it
-- again with new offsets starts from the user's automation, not from the previous result (pass
-- reset = true to forget them after the user edited their automation). A track without a Volume envelope
-- gets one (unity), so each track still has exactly one volume lane. Returns one line per track.
return function(spec)
  local key = spec.key or 'autoreaper_volume_offsets'
  local beat_ramp = spec.ramp_beats or 1

  local function track_for(ref)
    local bare = ref:gsub('[{}]', '')
    for i = 0, reaper.CountTracks(0) - 1 do
      local t = reaper.GetTrack(0, i)
      local _, name = reaper.GetTrackName(t)
      if reaper.GetTrackGUID(t):gsub('[{}]', '') == bare or name == ref then return t end
    end
    error('No track ' .. ref)
  end

  local function bar_time(bar) return reaper.TimeMap2_beatsToTime(0, 0, bar - 1) end

  -- Offset curve in dB: steps at section starts, reached by a ramp of ramp_beats (or from an explicit bar).
  local function curve(offsets)
    local points, previous = {}, 0
    for _, o in ipairs(offsets) do
      local at = bar_time(o[1])
      if o[3] then
        points[#points + 1] = {bar_time(o[3]), previous}
      elseif #points > 0 then
        local qn = reaper.TimeMap2_timeToQN(0, at)
        points[#points + 1] = {reaper.TimeMap2_QNToTime(0, qn - beat_ramp), previous}
      end
      points[#points + 1] = {math.max(0, at), o[2]}
      previous = o[2]
    end
    return points
  end

  local function offset_at(points, t)
    if t <= points[1][1] then return points[1][2] end
    for i = 2, #points do
      if t <= points[i][1] then
        local a, b = points[i - 1], points[i]
        if b[1] == a[1] then return b[2] end
        return a[2] + (b[2] - a[2]) * (t - a[1]) / (b[1] - a[1])
      end
    end
    return points[#points][2]
  end

  local report = {}
  for _, group in ipairs(spec.groups) do
    local points = curve(group.offsets)
    for _, ref in ipairs(group.tracks) do
      local track = track_for(ref)
      local guid = reaper.GetTrackGUID(track)
      local _, name = reaper.GetTrackName(track)
      local env, created = reaper.GetTrackEnvelopeByName(track, 'Volume'), false
      if not env then
        local _, chunk = reaper.GetTrackStateChunk(track, '', false)
        local block = '<VOLENV2\nEGUID ' .. reaper.genGuid('') .. '\nACT 1 -1\nVIS 1 1 1\nLANEHEIGHT 0 0\nARM 0\n' ..
          'DEFSHAPE 0 -1 -1\nVOLTYPE 1\nPT 0 1 0\n>\n'
        local at = chunk:find('<FXCHAIN') or chunk:find('<ITEM') or (#chunk - 1)
        reaper.SetTrackStateChunk(track, chunk:sub(1, at - 1) .. block .. chunk:sub(at), false)
        env, created = reaper.GetTrackEnvelopeByName(track, 'Volume'), true
      end
      local mode = reaper.GetEnvelopeScalingMode(env)
      if spec.reset then reaper.SetExtState(key, guid, '', false) end
      local saved, original = reaper.GetExtState(key, guid), {}
      if saved == '' then
        for p = 0, reaper.CountEnvelopePoints(env) - 1 do
          local _, time, value, shape, tension = reaper.GetEnvelopePoint(env, p)
          original[#original + 1] = {time, reaper.ScaleFromEnvelopeMode(mode, value), shape, tension}
        end
        if created then original = {{0, 1, 0, 0}} end
        local rows = {}
        for _, o in ipairs(original) do rows[#rows + 1] = string.format('%.9f,%.12f,%d,%.6f', o[1], o[2], o[3], o[4]) end
        reaper.SetExtState(key, guid, table.concat(rows, ';'), false)
      else
        for row in saved:gmatch('[^;]+') do
          local a, b, c, d = row:match('([^,]+),([^,]+),([^,]+),([^,]+)')
          original[#original + 1] = {tonumber(a), tonumber(b), tonumber(c), tonumber(d)}
        end
      end
      -- The user's envelope between its points: linear in fader space, square shape holds.
      local function original_at(t)
        if #original == 0 then return 1 end
        if t <= original[1][1] then return original[1][2] end
        for i = 2, #original do
          if t <= original[i][1] then
            local a, b = original[i - 1], original[i]
            if a[3] == 1 then return a[2] end
            if b[1] == a[1] then return b[2] end
            local fa, fb = reaper.ScaleToEnvelopeMode(mode, a[2]), reaper.ScaleToEnvelopeMode(mode, b[2])
            return reaper.ScaleFromEnvelopeMode(mode, fa + (fb - fa) * (t - a[1]) / (b[1] - a[1]))
          end
        end
        return original[#original][2]
      end
      -- Union of the user's points (jumps kept in order) and the offset breakpoints.
      local all = {}
      for i, o in ipairs(original) do all[#all + 1] = {o[1], o[2], o[3], o[4], i} end
      for _, c in ipairs(points) do all[#all + 1] = {c[1], original_at(c[1]), 0, 0, 1e9} end
      table.sort(all, function(a, b) if a[1] ~= b[1] then return a[1] < b[1] end return a[5] < b[5] end)
      reaper.DeleteEnvelopePointRange(env, -1, 1e9)
      for _, p in ipairs(all) do
        local gain = p[2] * 10 ^ (offset_at(points, p[1]) / 20)
        reaper.InsertEnvelopePoint(env, p[1], reaper.ScaleToEnvelopeMode(mode, gain), p[3], p[4], false, true)
      end
      reaper.Envelope_SortPoints(env)
      local lanes = 0
      for e = 0, reaper.CountTrackEnvelopes(track) - 1 do
        local _, lane = reaper.GetEnvelopeName(reaper.GetTrackEnvelope(track, e))
        if lane:match('^Volume') or lane:match('^Trim') then lanes = lanes + 1 end
      end
      report[#report + 1] = string.format('%s: %d original points -> %d, volume lanes %d%s', name, #original,
        reaper.CountEnvelopePoints(env), lanes, created and ' (new envelope)' or '')
    end
  end
  return report
end
