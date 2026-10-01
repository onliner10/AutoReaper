local p, filename = reaper.EnumProjects(-1,'')
local a,b = reaper.GetSet_LoopTimeRange2(p,false,false,0,0,false)
local tracks={}
for i=0,reaper.CountTracks(p)-1 do
  local tr=reaper.GetTrack(p,i)
  local _, name=reaper.GetTrackName(tr)
  local t={index=i,guid=reaper.GetTrackGUID(tr),name=name,
    mute=reaper.GetMediaTrackInfo_Value(tr,'B_MUTE'),solo=reaper.GetMediaTrackInfo_Value(tr,'I_SOLO'),
    volume=reaper.GetMediaTrackInfo_Value(tr,'D_VOL'),pan=reaper.GetMediaTrackInfo_Value(tr,'D_PAN'),
    folder_depth=reaper.GetMediaTrackInfo_Value(tr,'I_FOLDERDEPTH'),items={},fx={}}
  for f=0,reaper.TrackFX_GetCount(tr)-1 do
    local _,n=reaper.TrackFX_GetFXName(tr,f,'')
    t.fx[#t.fx+1]={index=f,name=n,enabled=reaper.TrackFX_GetEnabled(tr,f),guid=reaper.TrackFX_GetFXGUID(tr,f)}
  end
  t.item_count=reaper.CountTrackMediaItems(tr)
  for j=0,math.min(t.item_count,200)-1 do
    local item=reaper.GetTrackMediaItem(tr,j)
    local _,guid=reaper.GetSetMediaItemInfo_String(item,'GUID','',false)
    local take=reaper.GetActiveTake(item)
    local row={index=j,guid=guid,position_seconds=reaper.GetMediaItemInfo_Value(item,'D_POSITION'),length_seconds=reaper.GetMediaItemInfo_Value(item,'D_LENGTH')}
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
    t.items[#t.items+1]=row
  end
  t.items_complete=t.item_count<=200
  tracks[#tracks+1]=t
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
  coverage='All tracks and markers, up to 200 items/track and 512 notes/take. MIDI is source data; looped occurrences are not expanded.'}
