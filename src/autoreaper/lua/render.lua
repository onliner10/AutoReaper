-- Variables supplied by the Python transaction: output_dir, pattern, start_time,
-- end_time, isolate_guids, bypass_fx ({track=GUID, fx=FX GUID}). Render master with all
-- routing and effects; the bypass_fx plugins are off for this render only.
local p=reaper.EnumProjects(-1,'')
local project_state_before=reaper.GetProjectStateChangeCount(p)
-- Offline rendering needs a stopped transport. Playing or paused is stopped here
-- and reported; a recording is never stopped because that would end the user's take.
local _,speed=reaper.get_config_var_string('projrenderlimit')
assert(tonumber(speed)==0,'Choose Full-speed Offline once in REAPER Render settings; realtime rendering is never used')
local play_state=reaper.GetPlayStateEx(p)
assert(play_state&4==0,'REAPER is recording. Stop the recording yourself; AutoReaper never stops a recording to render.')
local stopped_playback=false
if play_state~=0 then
  reaper.OnStopButtonEx(p)
  assert(reaper.GetPlayStateEx(p)==0,'REAPER did not stop playback before offline rendering')
  stopped_playback=true
end
local numeric={RENDER_SETTINGS=0,RENDER_BOUNDSFLAG=0,RENDER_STARTPOS=start_time,
 RENDER_ENDPOS=end_time,RENDER_CHANNELS=2,RENDER_SRATE=48000,RENDER_TAILFLAG=0,
 RENDER_ADDTOPROJ=0,RENDER_DITHER=0,RENDER_NORMALIZE=0}
-- Ultraschall's WAVE render-cfg layout uses bytes "evaw", bit depth, flags,
-- large-file mode.  32 is documented as IEEE-754 32-bit float.  Keep the
-- native render scale so measurements can observe samples above 0 dBFS; the
-- Python delivery path creates a separate safe PCM audition copy.
local strings={RENDER_FILE=output_dir,RENDER_PATTERN=pattern,RENDER_FORMAT='ZXZhdyAAAAA=',RENDER_FORMAT2=''}
local old_n,old_s,track_state={},{},{}
for k in pairs(numeric) do old_n[k]=reaper.GetSetProjectInfo(p,k,0,false) end
for k in pairs(strings) do local _,v=reaper.GetSetProjectInfo_String(p,k,'',false); old_s[k]=v end
-- Some models strip the braces from GetTrackGUID-style GUIDs; match on the
-- bare hex body so both spellings find the track.
local function guid_key(value) return (value:gsub('[{}]','')) end
local wanted={}; for _,guid in ipairs(isolate_guids) do wanted[guid_key(guid)]=true end
for _,guid in ipairs(exclude_guids) do wanted[guid_key(guid)]=true end
local targets,solos={},{}
for i=0,reaper.CountTracks(p)-1 do
  local t=reaper.GetTrack(p,i); local guid=reaper.GetTrackGUID(t); local _,name=reaper.GetTrackName(t)
  track_state[#track_state+1]={track=t,mute=reaper.GetMediaTrackInfo_Value(t,'B_MUTE'),solo=reaper.GetMediaTrackInfo_Value(t,'I_SOLO')}
  if wanted[guid_key(guid)] then targets[#targets+1]={guid=guid,track=name}; wanted[guid_key(guid)]=nil end
  if reaper.GetMediaTrackInfo_Value(t,'I_SOLO')~=0 then solos[#solos+1]=name end
end
assert(next(wanted)==nil,'Requested track GUID is no longer present')
-- Audio sources REAPER reports with no length or sample rate are offline: they render as
-- silence while the files exist (e.g. "set media offline when inactive"), so name them.
local offline,seen={},{}
for i=0,reaper.CountMediaItems(p)-1 do
  local item=reaper.GetMediaItem(p,i)
  local position=reaper.GetMediaItemInfo_Value(item,'D_POSITION')
  local take=reaper.GetActiveTake(item)
  if take and not reaper.TakeIsMIDI(take) and position<end_time
      and position+reaper.GetMediaItemInfo_Value(item,'D_LENGTH')>start_time
      and reaper.GetMediaTrackInfo_Value(reaper.GetMediaItemTrack(item),'B_MUTE')==0 then
    local source=reaper.GetMediaItemTake_Source(take)
    local kind=reaper.GetMediaSourceType(source,'')
    if kind~='EMPTY' and kind~='CLICK' and kind~='VIDEOEFFECT' then
      local length=reaper.GetMediaSourceLength(source)
      if (length or 0)<=0 or reaper.GetMediaSourceSampleRate(source)==0 then
        local file=reaper.GetMediaSourceFileName(source,'')
        if not seen[file] then seen[file]=true; offline[#offline+1]=file end
      end
    end
  end
end
local bypassed={}
for _,want in ipairs(bypass_fx) do
  local found=false
  for _,s in ipairs(track_state) do
    if guid_key(reaper.GetTrackGUID(s.track))==guid_key(want.track) then
      for f=0,reaper.TrackFX_GetCount(s.track)-1 do
        if guid_key(reaper.TrackFX_GetFXGUID(s.track,f))==guid_key(want.fx) then
          local _,name=reaper.TrackFX_GetFXName(s.track,f,'')
          bypassed[#bypassed+1]={track=s.track,index=f,enabled=reaper.TrackFX_GetEnabled(s.track,f),name=name}
          found=true
        end
      end
    end
  end
  assert(found,'A plugin to bypass is no longer present')
end
local function restore()
  for k,v in pairs(old_n) do reaper.GetSetProjectInfo(p,k,v,true) end
  for k,v in pairs(old_s) do reaper.GetSetProjectInfo_String(p,k,v,true) end
  for _,s in ipairs(track_state) do
    reaper.SetMediaTrackInfo_Value(s.track,'B_MUTE',s.mute)
    reaper.SetMediaTrackInfo_Value(s.track,'I_SOLO',s.solo)
  end
  for _,b in ipairs(bypassed) do reaper.TrackFX_SetEnabled(b.track,b.index,b.enabled) end
end
local ok,err=xpcall(function()
  for k,v in pairs(numeric) do reaper.GetSetProjectInfo(p,k,v,true) end
  for k,v in pairs(strings) do assert(reaper.GetSetProjectInfo_String(p,k,v,true),'Render setting rejected: '..k) end
  if #isolate_guids>0 then
    local chosen={}; for _,guid in ipairs(isolate_guids) do chosen[guid_key(guid)]=true end
    for _,s in ipairs(track_state) do
      reaper.SetMediaTrackInfo_Value(s.track,'I_SOLO',chosen[guid_key(reaper.GetTrackGUID(s.track))] and 2 or 0)
      if chosen[guid_key(reaper.GetTrackGUID(s.track))] then
        reaper.SetMediaTrackInfo_Value(s.track,'B_MUTE',0)
        local parent=reaper.GetParentTrack(s.track)
        while parent do reaper.SetMediaTrackInfo_Value(parent,'B_MUTE',0); parent=reaper.GetParentTrack(parent) end
      end
    end
  end
  local excluded={}; for _,guid in ipairs(exclude_guids) do excluded[guid_key(guid)]=true end
  for _,s in ipairs(track_state) do
    if excluded[guid_key(reaper.GetTrackGUID(s.track))] then reaper.SetMediaTrackInfo_Value(s.track,'B_MUTE',1) end
  end
  for _,b in ipairs(bypassed) do reaper.TrackFX_SetEnabled(b.track,b.index,false) end
  reaper.Main_OnCommand(42230,0)
end,debug.traceback)
restore()
local restored=true
for k,v in pairs(old_n) do if reaper.GetSetProjectInfo(p,k,0,false)~=v then restored=false end end
for k,v in pairs(old_s) do local _,now=reaper.GetSetProjectInfo_String(p,k,'',false); if now~=v then restored=false end end
for _,b in ipairs(bypassed) do if reaper.TrackFX_GetEnabled(b.track,b.index)~=b.enabled then restored=false end end
for _,s in ipairs(track_state) do
  if reaper.GetMediaTrackInfo_Value(s.track,'B_MUTE')~=s.mute or reaper.GetMediaTrackInfo_Value(s.track,'I_SOLO')~=s.solo then restored=false end
end
assert(restored,'Render settings or mixer restoration failed')
assert(ok,err)
local bar_grid={}
local _,measure=reaper.TimeMap2_timeToBeats(p,start_time)
for m=measure,measure+9999 do
  local s,q0,q1,num,den,tempo=reaper.TimeMap_GetMeasureInfo(p,m)
  local e=reaper.TimeMap_GetMeasureInfo(p,m+1)
  if s>=end_time then break end
  bar_grid[#bar_grid+1]={bar=m+1,start_seconds=s,end_seconds=e,qn=q1-q0,numerator=num,denominator=den,tempo=tempo}
end
return {state_restored=restored,stopped_playback=stopped_playback,render_mode='full_speed_offline',capture_point='master post-FX',
 project_state_change_count_before=project_state_before,
 project_state_change_count_after=reaper.GetProjectStateChangeCount(p),
 bar_grid_complete=#bar_grid>0 and bar_grid[#bar_grid].end_seconds>=end_time,
 targets=targets,excluded_tracks=exclude_guids,offline_sources=offline,
 bypassed_fx=(function() local r={} for _,b in ipairs(bypassed) do r[#r+1]=b.name end return r end)(),bar_grid=bar_grid,soloed_tracks=#isolate_guids==0 and solos or isolate_guids,
 listening_scope=#isolate_guids>0 and 'isolated tracks through master, sends and parent routing' or (#solos>0 and 'existing solo configuration through master' or 'current master mix')}
