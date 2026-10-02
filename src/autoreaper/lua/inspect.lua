local p, filename = reaper.EnumProjects(-1,'')
local a,b = reaper.GetSet_LoopTimeRange2(p,false,false,0,0,false)
-- Filters (set by the server): words in the track name, an item time window, items off.
local words={}
for w in (track_query or ''):lower():gmatch('%S+') do words[#words+1]=w end
local function wanted(name)
  local lowered=name:lower()
  for _,w in ipairs(words) do if not lowered:find(w,1,true) then return false end end
  return true
end
local window_start=from_bar and reaper.TimeMap2_beatsToTime(p,0,from_bar-1) or nil
local window_end=to_bar and reaper.TimeMap2_beatsToTime(p,0,to_bar-1) or nil
local function in_window(position,length)
  return (not window_start or position+length>window_start) and (not window_end or position<window_end)
end
local function item_row(item,j,position,length)
  local _,guid=reaper.GetSetMediaItemInfo_String(item,'GUID','',false)
  local take=reaper.GetActiveTake(item)
  local row={index=j,guid=guid,position_seconds=position,length_seconds=length}
  if take then
    row.take_name=reaper.GetTakeName(take); row.midi=reaper.TakeIsMIDI(take)
    if row.midi then
      local _,notes,cc,text=reaper.MIDI_CountEvts(take)
      row.note_count=notes; row.cc_count=cc; row.notes={}
      if include_notes then
        for n=0,math.min(notes,512)-1 do
          local _,sel,muted,s,e,ch,pitch,vel=reaper.MIDI_GetNote(take,n)
          row.notes[#row.notes+1]={index=n,pitch=pitch,velocity=vel,channel=ch,selected=sel,muted=muted,start_ppq=s,end_ppq=e,
            start_seconds=reaper.MIDI_GetProjTimeFromPPQPos(take,s),end_seconds=reaper.MIDI_GetProjTimeFromPPQPos(take,e)}
        end
      end
      row.notes_complete=include_notes and notes<=512
    end
  end
  return row
end
local tracks={}
for i=0,reaper.CountTracks(p)-1 do
  local tr=reaper.GetTrack(p,i)
  local _, name=reaper.GetTrackName(tr)
  if wanted(name) then
    local t={index=i,guid=reaper.GetTrackGUID(tr),name=name,
      mute=reaper.GetMediaTrackInfo_Value(tr,'B_MUTE'),solo=reaper.GetMediaTrackInfo_Value(tr,'I_SOLO'),
      volume=reaper.GetMediaTrackInfo_Value(tr,'D_VOL'),pan=reaper.GetMediaTrackInfo_Value(tr,'D_PAN'),
      folder_depth=reaper.GetMediaTrackInfo_Value(tr,'I_FOLDERDEPTH'),items={},fx={}}
    for f=0,reaper.TrackFX_GetCount(tr)-1 do
      local _,n=reaper.TrackFX_GetFXName(tr,f,'')
      t.fx[#t.fx+1]={index=f,name=n,enabled=reaper.TrackFX_GetEnabled(tr,f),guid=reaper.TrackFX_GetFXGUID(tr,f)}
    end
    t.item_count=reaper.CountTrackMediaItems(tr)
    local matched=0
    for j=0,(include_items and t.item_count or 0)-1 do
      local item=reaper.GetTrackMediaItem(tr,j)
      local position=reaper.GetMediaItemInfo_Value(item,'D_POSITION')
      local length=reaper.GetMediaItemInfo_Value(item,'D_LENGTH')
      if in_window(position,length) then
        matched=matched+1
        if matched<=200 then t.items[#t.items+1]=item_row(item,j,position,length) end
      end
    end
    t.items_complete=include_items and matched<=200
    tracks[#tracks+1]=t
  end
end
local markers={}
local _,nm,nr=reaper.CountProjectMarkers(p)
for i=0,nm+nr-1 do
  local _,region,pos,ending,name,id,color=reaper.EnumProjectMarkers3(p,i)
  markers[#markers+1]={id=id,region=region,position_seconds=pos,end_seconds=ending,name=name,color=color}
end
return {filename=filename,tracks=tracks,track_count=reaper.CountTracks(p),markers=markers,
  cursor_seconds=reaper.GetCursorPositionEx(p),length_seconds=reaper.GetProjectLength(p),
  time_selection={start_seconds=a,end_seconds=b},tempo=reaper.Master_GetTempo(),
  state_change_count=reaper.GetProjectStateChangeCount(p),play_state=reaper.GetPlayStateEx(p),
  filters={track_query=track_query,from_bar=from_bar,to_bar=to_bar,include_items=include_items},
  coverage='Tracks matching track_query (all when empty) and all markers; up to 200 items/track (within from_bar..to_bar when given) and 512 notes/take. MIDI is source data; looped occurrences are not expanded.'}
