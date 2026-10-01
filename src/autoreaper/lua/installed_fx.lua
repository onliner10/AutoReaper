local options=...
local terms={}
for term in options.query:lower():gmatch('%S+') do terms[#terms+1]=term end
local rows,matched={},0
local complete=false
for i=0,19999 do
  local ok,name,ident=reaper.EnumInstalledFX(i)
  if not ok then complete=true;break end
  local match=type(name)=='string' and name:find('%S')~=nil
  if match then
    for _,term in ipairs(terms) do
      if not name:lower():find(term,1,true) then match=false;break end
    end
  end
  if match then
    matched=matched+1
    if matched>options.offset and #rows<options.limit then
      rows[#rows+1]={name=name,ident=ident}
    end
  end
end
return {plugins=rows,matched_count=matched,offset=options.offset,
  next_offset=matched>options.offset+#rows and options.offset+#rows or nil,
  scan_complete=complete,read_only=true,
  meaning='Installed identities only; no plugin was loaded or changed. Installation does not establish a license.'}
