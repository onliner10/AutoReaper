local opts=...
local p,filename=reaper.EnumProjects(-1,'')
local before=reaper.GetProjectStateChangeCount(p)
local start_time=reaper.TimeMap_GetMeasureInfo(p,opts.start_bar-1)
local end_time=reaper.TimeMap_GetMeasureInfo(p,opts.end_bar-1)
assert(start_time>=0 and end_time>start_time,'Invalid native listening interval')
local grid={}
for m=opts.start_bar-1,opts.end_bar-2 do
  local s,q0,q1,num,den,tempo=reaper.TimeMap_GetMeasureInfo(p,m)
  local e=reaper.TimeMap_GetMeasureInfo(p,m+1)
  grid[#grid+1]={bar=m+1,start_seconds=s,end_seconds=e,qn=q1-q0,numerator=num,denominator=den,tempo=tempo}
end
local after=reaper.GetProjectStateChangeCount(p)
return {schema='autoreaper.listening-range.v1',read_only=true,undo_block_opened=false,
  project_id=tostring(p)..'|'..filename,state_change_count=before,state_change_count_after=after,stable=before==after,
  start_bar=opts.start_bar,end_bar=opts.end_bar,start_seconds=start_time,end_seconds=end_time,
  bar_grid=grid,bar_grid_complete=true,
  position_origin='Native measure 0 is bar 1; custom ruler offsets are not applied.'}
