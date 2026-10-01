-- Application-owned, read-only signal-flow inspection. Reads track identities, folder
-- hierarchy, sources, plugin identities (names only, no parameters) and sends.
-- Receives are the other end of a send and are derived from the sends by the caller.
local project,filename=reaper.EnumProjects(-1,'')
local state_before=reaper.GetProjectStateChangeCount(project)
local limits={tracks=256,fx_per_track=64,item_scan_per_track=5000,sends_per_track=64}

local function db(linear)
  if type(linear)~='number' or linear~=linear or linear<=0 then return -150 end
  return math.floor(math.max(-150,math.min(150,20*math.log(linear,10)))*10+0.5)/10
end

local function source_kind(take)
  if not take then return 'none' end
  if reaper.TakeIsMIDI(take) then return 'midi' end
  return 'audio'
end

-- REAPER stores channel selections as an offset in the low 10 bits and a width code above.
local function source_channels(raw)
  local code=math.floor(raw or -1)
  if code<0 then return nil end
  local width=code>>10
  return {offset=code&1023,count=width==0 and 2 or (width==1 and 1 or width*2)}
end

local function fx_rows(track,instrument)
  local rows={}
  local total=reaper.TrackFX_GetCount(track)
  for index=0,math.min(total,limits.fx_per_track)-1 do
    local _,name=reaper.TrackFX_GetFXName(track,index,'')
    rows[#rows+1]={name=name,guid=reaper.TrackFX_GetFXGUID(track,index),
      bypassed=not reaper.TrackFX_GetEnabled(track,index),
      offline=reaper.TrackFX_GetOffline(track,index),instrument=index==instrument or nil}
  end
  return rows,total
end

local function send_rows(track)
  local rows={}
  local total=reaper.GetTrackNumSends(track,0)
  for index=0,math.min(total,limits.sends_per_track)-1 do
    local function info(key) return reaper.GetTrackSendInfo_Value(track,0,index,key) end
    local destination=info('P_DESTTRACK')
    local destination_raw=math.floor(info('I_DSTCHAN'))
    local audio=source_channels(info('I_SRCCHAN'))
    rows[#rows+1]={to=destination and reaper.GetTrackGUID(destination) or nil,
      audio=audio~=nil,source_offset=audio and audio.offset or nil,source_count=audio and audio.count or nil,
      -- A MIDI source-channel code of 31 means no MIDI on this send.
      midi=(math.floor(info('I_MIDIFLAGS'))&31)~=31,
      destination_offset=destination_raw&1023,destination_mono=(destination_raw&1024)~=0,
      volume_db=db(info('D_VOL')),mute=info('B_MUTE')>0.5,mode=math.floor(info('I_SENDMODE'))}
  end
  return rows,total
end

local tracks={}
local track_count=reaper.CountTracks(project)
for index=0,math.min(track_count,limits.tracks)-1 do
  local track=reaper.GetTrack(project,index)
  local _,name=reaper.GetTrackName(track)
  local parent=reaper.GetParentTrack(track)
  local instrument=reaper.TrackFX_GetInstrument(track)
  local fx,fx_total=fx_rows(track,instrument)
  local sends,send_total=send_rows(track)
  local items={midi=0,audio=0,other=0,muted=0}
  local item_count=reaper.CountTrackMediaItems(track)
  for item_index=0,math.min(item_count,limits.item_scan_per_track)-1 do
    local item=reaper.GetTrackMediaItem(track,item_index)
    local kind=source_kind(reaper.GetActiveTake(item))
    if kind=='none' then kind='other' end
    items[kind]=items[kind]+1
    if reaper.GetMediaItemInfo_Value(item,'B_MUTE')>0.5 then items.muted=items.muted+1 end
  end
  tracks[#tracks+1]={index=index,guid=reaper.GetTrackGUID(track),name=name,
    parent_guid=parent and reaper.GetTrackGUID(parent) or nil,
    folder_depth=math.floor(reaper.GetMediaTrackInfo_Value(track,'I_FOLDERDEPTH')),
    mute=reaper.GetMediaTrackInfo_Value(track,'B_MUTE')>0.5,
    solo=reaper.GetMediaTrackInfo_Value(track,'I_SOLO')~=0,
    phase_inverted=reaper.GetMediaTrackInfo_Value(track,'B_PHASE')>0.5,
    channels=math.floor(reaper.GetMediaTrackInfo_Value(track,'I_NCHAN')),
    -- Record input matters only while the track is armed: it is then a live source.
    armed=reaper.GetMediaTrackInfo_Value(track,'I_RECARM')>0.5,
    record_input=math.floor(reaper.GetMediaTrackInfo_Value(track,'I_RECINPUT')),
    main_send=reaper.GetMediaTrackInfo_Value(track,'B_MAINSEND')>0.5,
    main_send_offset=math.floor(reaper.GetMediaTrackInfo_Value(track,'C_MAINSEND_OFFS')),
    items=items,item_count=item_count,items_complete=item_count<=limits.item_scan_per_track,
    fx=fx,fx_total=fx_total,sends=sends,send_total=send_total,
    hardware_output_count=reaper.GetTrackNumSends(track,1)}
end

local master=reaper.GetMasterTrack(project)
local master_fx,master_fx_total=fx_rows(master,-1)
local state_after=reaper.GetProjectStateChangeCount(project)
return {schema='autoreaper.signal-flow.v1',read_only=true,undo_block_opened=false,
  state_change_stable=state_before==state_after,
  project={project_id=tostring(project)..'|'..filename,filename=filename,
    state_change_count_before=state_before,state_change_count_after=state_after},
  track_count=track_count,tracks=tracks,
  master={guid=reaper.GetTrackGUID(master),mute=reaper.GetMediaTrackInfo_Value(master,'B_MUTE')>0.5,
    fx=master_fx,fx_total=master_fx_total},
  limits=limits}
