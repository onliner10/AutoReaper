-- Execute native REAPER queries without exposing writes or host globals.
-- The server wraps this module around the caller's code; that code only sees `env`.
local native = reaper
local reads = [[
APIExists GetAppVersion GetOS GetResourcePath GetProjectPath GetProjectName
EnumProjects GetProjectStateChangeCount CountTracks GetTrack GetMasterTrack
CountSelectedTracks GetSelectedTrack CountSelectedTracks2 GetSelectedTrack2
GetTrackName GetTrackGUID GetTrackColor GetMediaTrackInfo_Value GetParentTrack
GetTrackDepth GetTrackNumSends GetTrackSendInfo_Value GetTrackReceiveName
GetTrackSendName GetTrackNumMediaItems CountTrackMediaItems GetTrackMediaItem
CountMediaItems GetMediaItem CountSelectedMediaItems GetSelectedMediaItem
GetMediaItemInfo_Value GetMediaItemTrack CountTakes GetTake GetActiveTake
GetTakeName GetMediaItemTakeInfo_Value GetMediaItemTake_Source TakeIsMIDI
GetMediaSourceFileName GetMediaSourceLength GetMediaSourceType GetMediaSourceNumChannels
GetMediaSourceSampleRate GetMediaSourceParent
TrackFX_GetCount TrackFX_GetRecCount TrackFX_GetFXName TrackFX_GetFXGUID
TrackFX_GetEnabled TrackFX_GetOffline TrackFX_GetNumParams TrackFX_GetParam
TrackFX_GetParamEx TrackFX_GetParamNormalized TrackFX_GetParamName
TrackFX_GetParamIdent TrackFX_GetParamFromIdent TrackFX_GetFormattedParamValue
TrackFX_FormatParamValue TrackFX_FormatParamValueNormalized TrackFX_GetParameterStepSizes
TrackFX_GetNamedConfigParm TrackFX_GetPinMappings TrackFX_GetIOSize
TrackFX_GetPreset TrackFX_GetPresetIndex TrackFX_GetPresetFileName
TakeFX_GetCount TakeFX_GetFXName TakeFX_GetFXGUID TakeFX_GetEnabled TakeFX_GetOffline
TakeFX_GetNumParams TakeFX_GetParam TakeFX_GetParamEx TakeFX_GetParamNormalized
TakeFX_GetParamName TakeFX_GetParamIdent TakeFX_GetParamFromIdent
TakeFX_GetFormattedParamValue TakeFX_FormatParamValue TakeFX_FormatParamValueNormalized
TakeFX_GetParameterStepSizes TakeFX_GetNamedConfigParm TakeFX_GetPinMappings TakeFX_GetIOSize
MIDI_CountEvts MIDI_GetNote MIDI_GetCC MIDI_GetTextSysexEvt MIDI_GetEvt MIDI_GetAllEvts
MIDI_GetHash MIDI_GetProjTimeFromPPQPos MIDI_GetPPQPosFromProjTime
MIDI_GetProjQNFromPPQPos MIDI_GetPPQPosFromProjQN
MIDI_EnumSelNotes MIDI_EnumSelCC MIDI_EnumSelTextSysexEvts
CountTrackEnvelopes GetTrackEnvelope GetTrackEnvelopeByName GetTrackEnvelopeByChunkName
GetEnvelopeName GetEnvelopeInfo_Value CountEnvelopePoints CountEnvelopePointsEx
GetEnvelopePoint GetEnvelopePointEx GetEnvelopePointByTime GetEnvelopePointByTimeEx
Envelope_Evaluate CountAutomationItems
CountTempoTimeSigMarkers GetTempoTimeSigMarker TimeMap_GetTimeSigAtTime
TimeMap2_timeToBeats TimeMap2_beatsToTime TimeMap2_timeToQN TimeMap2_QNToTime
TimeMap_GetDividedBpmAtTime TimeMap_GetMeasureInfo TimeMap_GetMetronomePattern
CountProjectMarkers EnumProjectMarkers EnumProjectMarkers2 EnumProjectMarkers3
GetProjectLength GetCursorPosition GetCursorPositionEx GetPlayState GetPlayStateEx
GetPlayPosition GetPlayPositionEx GetPlayPosition2 GetPlayPosition2Ex Master_GetTempo
GetExtState HasExtState GetProjExtState EnumProjExtState
EnumInstalledFX EnumPitchShiftModes EnumPitchShiftSubModes
ColorFromNative ColorToNative ValidatePtr ValidatePtr2
BR_GetMediaTrackByGUID BR_GetMediaItemByGUID BR_GetMediaItemTakeByGUID
BR_GetMediaTrackSendInfo_Track BR_GetMediaItemGUID BR_GetMediaItemTakeGUID
TimeMap_QNToTime TimeMap_timeToQN TimeMap_QNToTime_abs TimeMap_timeToQN_abs
TimeMap_QNToMeasures GetItemStateChunk GetTrackStateChunk GetEnvelopeStateChunk
GetMediaItemTake_Track TrackFX_GetInstrument GetTrackMIDINoteName GetTrackMIDINoteNameEx
GetTrackUIVolPan GetTrackState GetTrackAutomationMode kbd_getTextFromCmd CF_GetCommandText
]]
-- These mixed APIs are read-only ONLY when their write flag is literally false.
local mixed = {
  GetSetMediaTrackInfo_String=4, GetSetMediaItemInfo_String=4,
  GetSetMediaItemTakeInfo_String=4, GetSetEnvelopeInfo_String=4,
  GetSetTrackSendInfo_String=6, GetSetProjectInfo=4, GetSetProjectInfo_String=4,
  GetSet_LoopTimeRange=1, GetSet_LoopTimeRange2=2,
  GetSet_ArrangeView2=2, GetSetAutomationItemInfo=5,
}
return function(source)
  local blocked = false
  local function deny(name)
    blocked = true
    error('Requires permission review: '..tostring(name), 0)
  end
  local api = {}
  for name in reads:gmatch('%S+') do api[name] = native[name] end
  api.GetMediaItemInfo_Value=function(item,parameter)
    assert(parameter~='D_STARTPOS','D_STARTPOS is not an item property. Use D_POSITION.')
    return native.GetMediaItemInfo_Value(item,parameter)
  end
  for name, flag in pairs(mixed) do
    local fn = native[name]
    if fn then
      api[name] = function(...)
        if select(flag, ...) ~= false then return deny(name) end
        return fn(...)
      end
    end
  end
  setmetatable(api, {__index=function(_, name) return deny('reaper.'..tostring(name)) end})
  local function copy(lib, names)
    local out={}
    for name in names:gmatch('%S+') do out[name]=lib[name] end
    return out
  end
  local env = {
    reaper=api, assert=assert, error=error, ipairs=ipairs, pairs=pairs, next=next,
    tonumber=tonumber, tostring=tostring, type=type, select=select,
    math=copy(math, 'abs acos asin atan ceil cos deg exp floor fmod huge log max maxinteger min mininteger modf pi rad sin sqrt tan tointeger type ult'),
    string=copy(string, 'byte char find format gmatch gsub len lower match pack packsize rep reverse sub unpack upper'),
    table=copy(table, 'concat insert move pack remove sort unpack'),
    utf8=copy(utf8, 'char charpattern codepoint codes len offset'),
  }
  -- No _G, debug, io, os, package, require, load, metatables, coroutine or
  -- protected calls. User functions/aliases/loops operate only on these values.
  setmetatable(env, {__index=function(_, name) return deny(name) end})
  local fn, syntax_error = load(source, 'AutoReaper read-only eval', 't', env)
  if not fn then return {status='error', error=syntax_error} end
  local ok, value = pcall(fn)
  if blocked then return {status='needs_review'} end
  if not ok then return {status='error', error=tostring(value)} end
  return {status='complete', value=value}
end
