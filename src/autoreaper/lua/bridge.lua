-- AutoReaper Bridge: lets the AutoReaper MCP server (Claude Code plugin) run
-- ReaScript in this REAPER through a file mailbox. Run it once per REAPER
-- session from Actions; it keeps running in the background (defer loop).
-- This is full ReaScript with your user's privileges, not a security sandbox.
--
-- Mailbox folder, which must match the server's:
--   AUTOREAPER_MAILBOX env var, else AUTOREAPER_HOME/bridge,
--   else ~/.autoreaper/bridge (%USERPROFILE%/.autoreaper/bridge on Windows).
local sep = package.config:sub(1,1)
local function mailbox()
  local explicit = os.getenv('AUTOREAPER_MAILBOX')
  if explicit and explicit ~= '' then return explicit end
  local home = os.getenv('AUTOREAPER_HOME')
  if not home or home == '' then
    home = assert(os.getenv('USERPROFILE') or os.getenv('HOME'), 'Cannot find the home folder; set AUTOREAPER_HOME')
    home = home .. sep .. '.autoreaper'
  end
  return home .. sep .. 'bridge'
end
local root = mailbox():gsub('[/\\]+$', '')
reaper.RecursiveCreateDirectory(root, 0)
local function path(name) return root .. sep .. name end
local function json(value, seen)
  local t = type(value)
  if t == 'nil' then return 'null' end
  if t == 'boolean' then return tostring(value) end
  if t == 'number' then assert(value == value and math.abs(value) ~= math.huge, 'Non-finite JSON number'); return tostring(value) end
  if t == 'string' then return '"' .. value:gsub('[%z\1-\31\\"]', function(c)
    return string.format('\\u%04x', string.byte(c)) end) .. '"' end
  assert(t == 'table', 'Return only JSON-compatible data, not REAPER pointers')
  seen = seen or {}; assert(not seen[value], 'Circular result'); seen[value] = true
  local out, count, array = {}, 0, true
  for k in pairs(value) do count=count+1; if type(k)~='number' or k<1 or k%1~=0 then array=false end end
  if count ~= #value then array=false end
  if array and count>0 then
    for i=1,count do out[#out+1]=json(value[i], seen) end
  else
    for k,v in pairs(value) do out[#out+1]=json(tostring(k))..':'..json(v,seen) end
  end
  seen[value]=nil
  return (array and count>0 and '[' or '{') .. table.concat(out, ',') .. (array and count>0 and ']' or '}')
end
local function write(name, value)
  -- Encode before opening the file. A rejected value must not leave an open
  -- handle on the scratch path: Windows then refuses to rename over it and the
  -- receipt never reaches the caller.
  local encoded=json(value)
  local f=assert(io.open(path(name..'.tmp'),'wb')); f:write(encoded); f:close()
  os.remove(path(name)); assert(os.rename(path(name..'.tmp'),path(name)))
end
local function project_id()
  local p, filename = reaper.EnumProjects(-1, '')
  return tostring(p)..'|'..filename
end
local session = reaper.genGuid()
local last_heartbeat = 0
-- Id of the claimed request, so an unexpected error still leaves a receipt
-- instead of the caller waiting out its whole timeout.
local current = nil
-- Exactly one bridge may serve the mailbox. Two instances would both publish a
-- heartbeat and race to claim requests, so the caller's session would randomly
-- belong to the other one. The newest start takes ownership; older instances
-- notice and retire instead of fighting over the mailbox.
local function claim_ownership()
  local f=assert(io.open(path('owner.txt'),'wb')); f:write(session); f:close()
end
local function owns_mailbox()
  local f=io.open(path('owner.txt'),'rb')
  if not f then return true end
  local owner=f:read('a'); f:close()
  return owner==session
end
local function step()
  if reaper.time_precise()-last_heartbeat > .5 then
    write('heartbeat.json', {session=session, timestamp=os.time(), project_id=project_id(), version=reaper.GetAppVersion(), protocol=2})
    last_heartbeat=reaper.time_precise()
  end
  local f=io.open(path('request.lua'),'rb')
  if f then
    f:close()
    -- Claim before execution: a crashed request is not automatically replayed.
    if os.rename(path('request.lua'),path('request.running')) then
      local loaded, req=pcall(dofile,path('request.running'))
      if loaded and type(req)=='table' and type(req.id)=='string' and req.id:match('^[a-f0-9]+$') then
        current=req.id
        local result={id=req.id, ok=false, project_id=project_id(), changed=false}
        if req.session ~= session then
          result.error='Bridge session changed'; result.outcome='not_dispatched'; result.partial_change_possible=false
        elseif req.project_id~='' and req.project_id~=project_id() then
          result.error='Active project changed'; result.outcome='not_dispatched'; result.partial_change_possible=false
        else
          local proj=reaper.EnumProjects(-1,'')
          local deadline=reaper.time_precise()+(req.timeout or 60)
          local env=setmetatable({reaper=reaper}, {__index=_G})
          local function run(source,name)
            local fn,err=load(source,name,'t',env)
            if not fn then return false,err end
            debug.sethook(function() if reaper.time_precise()>deadline then error('Lua CPU deadline exceeded') end end,'',100000)
            local ok,value=xpcall(fn,debug.traceback)
            debug.sethook()
            return ok,value
          end
          -- A caller may supply a side-effect-free validation program. It runs
          -- before loading/starting the mutating request, so rejected FX
          -- gestures do not open an empty Undo block or look partially applied.
          local preflight_ok,preflight_value=true,nil
          if type(req.preflight)=='string' and req.preflight~='' then
            preflight_ok,preflight_value=run(req.preflight,'AutoReaper preflight')
          end
          if not preflight_ok then
            result.error=tostring(preflight_value)
            result.outcome='not_dispatched'
            result.changed=false
            result.partial_change_possible=false
          else
            -- Compile before opening an Undo block as well. Syntax/load
            -- failures are therefore rejected without an edit wrapper.
            local fn,load_error=load(req.code,'AutoReaper request','t',env)
            if not fn then
              result.error=tostring(load_error)
              result.outcome='not_dispatched'
              result.changed=false
              result.partial_change_possible=false
            else
              local before=reaper.GetProjectStateChangeCount(proj)
              local undo_started=false
              if req.mutate then reaper.Undo_BeginBlock2(proj); undo_started=true end
              debug.sethook(function() if reaper.time_precise()>deadline then error('Lua CPU deadline exceeded') end end,'',100000)
              local ok,value=xpcall(fn,debug.traceback)
              debug.sethook()
              if req.mutate then reaper.Undo_EndBlock2(proj,req.label,-1); reaper.UpdateArrange() end
              result.ok=ok
              result.changed=reaper.GetProjectStateChangeCount(proj)~=before
              result.undo_label=req.mutate and req.label or nil
              if ok then
                result.result=value
                result.outcome=req.mutate and 'dispatched_unverified' or 'read_only'
              else
                result.error=tostring(value)
                result.outcome=req.mutate and 'partial_or_unknown' or 'not_dispatched'
                result.partial_change_possible=undo_started
              end
            end
          end
        end
        pcall(write,'heartbeat.json',{session=session,timestamp=os.time(),project_id=project_id(),version=reaper.GetAppVersion(),protocol=2})
        if not pcall(write,req.id..'.json',result) then
          -- The request itself ran to completion; only its return value was
          -- not JSON. Report the real outcome without that payload so a
          -- read-only probe is not misreported as a possibly partial edit and
          -- the caller can simply retry with serializable data.
          result.result=nil; result.ok=false
          result.error='Return only JSON-compatible data, not REAPER pointers'
          if not pcall(write,req.id..'.json',result) then
            pcall(write,req.id..'.json',{ok=false,id=req.id,error='Result serialization failed; inspect project before retry',outcome='partial_or_unknown',partial_change_possible=true})
          end
        end
        os.remove(path('request.running'))
        current=nil
      else
        -- Unreadable request: drop it so it cannot block every later one.
        os.remove(path('request.running'))
      end
    end
  end
end
-- An unexpected error in one pass must never take the bridge down: REAPER
-- would stop deferring and every later request would time out against a
-- stale heartbeat with no way back except restarting the action.
local function report(err)
  -- A log line, never a console window: the bridge runs unattended and must
  -- not steal focus from REAPER.
  local f=io.open(path('bridge-errors.log'),'ab')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S ')..tostring(err)..'\n'); f:close() end
end
local function loop()
  if not owns_mailbox() then return end
  local ok, err = pcall(step)
  if not ok then
    pcall(report, err)
    if current then
      pcall(write, current..'.json', {id=current, ok=false, outcome='partial_or_unknown', partial_change_possible=true,
        error='Bridge error: '..tostring(err)..'; inspect the project before retrying'})
      current=nil
    end
    pcall(os.remove, path('request.running'))
  end
  reaper.defer(loop)
end
reaper.atexit(function() if owns_mailbox() then os.remove(path('heartbeat.json')) end end)
claim_ownership()
loop()
