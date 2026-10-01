-- Variables supplied by the Python transaction: output_dir, pattern, start_time,
-- end_time, isolate_guids. Render master with all routing and effects.
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
local function restore()
  for k,v in pairs(old_n) do reaper.GetSetProjectInfo(p,k,v,true) end
  for k,v in pairs(old_s) do reaper.GetSetProjectInfo_String(p,k,v,true) end
  for _,s in ipairs(track_state) do
    reaper.SetMediaTrackInfo_Value(s.track,'B_MUTE',s.mute)
    reaper.SetMediaTrackInfo_Value(s.track,'I_SOLO',s.solo)
  end
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
  reaper.Main_OnCommand(42230,0)
end,debug.traceback)
restore()
local restored=true
for k,v in pairs(old_n) do if reaper.GetSetProjectInfo(p,k,0,false)~=v then restored=false end end
for k,v in pairs(old_s) do local _,now=reaper.GetSetProjectInfo_String(p,k,'',false); if now~=v then restored=false end end
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
 targets=targets,excluded_tracks=exclude_guids,bar_grid=bar_grid,soloed_tracks=#isolate_guids==0 and solos or isolate_guids,
 listening_scope=#isolate_guids>0 and 'isolated tracks through master, sends and parent routing' or (#solos>0 and 'existing solo configuration through master' or 'current master mix')}
