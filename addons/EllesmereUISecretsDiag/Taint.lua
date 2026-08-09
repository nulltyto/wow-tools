-------------------------------------------------------------------------------
--  Taint.lua  —  finding out what is tainted, and who did it
--
--    /euidiag taint <path> [field]     is this secure, and if not, whose fault
--    /euidiag taint scan <path> [d]    every insecure field on a table
--    /euidiag taint snap <label> <path>...   baseline before you do the thing
--    /euidiag taint diff <label>             what flipped after you did it
--    /euidiag taint watch <path> <field>     catch the exact moment it flips
--    /euidiag taint errors [off|clear]       blocked/forbidden action capture
--    /euidiag taint log [0|1|2]              the client's own taint log
--
--  The one fact all of this rests on: issecurevariable(table, field) returns
--  whether the value stored at that field was last written by secure code, and
--  the name of the addon that wrote it if not. Taint is sticky for the session
--  — nothing here clears it, and a run that starts with something already
--  insecure proves nothing. /reload first, then measure.
--
--  Reading a field is free of consequence; none of these tools write. The
--  scans use pcall around every index because a secret table errors on access.
-------------------------------------------------------------------------------
local ADDON_NAME, ns = ...

local format, sort, concat = string.format, table.sort, table.concat
local emit, outf, header, result = ns.emit, ns.outf, ns.header, ns.result
local Pad, ResolvePath, SortedKeys = ns.Pad, ns.ResolvePath, ns.SortedKeys

-------------------------------------------------------------------------------
-- The single reading everything else is built on
-------------------------------------------------------------------------------
-- Returns tag, detail, isSecure. isSecure is nil when the reading failed, which
-- is a third state and must not be collapsed into "insecure" — an unreadable
-- field and a tainted one call for completely different next steps.
local function readTaint(tbl, field)
    if not issecurevariable then return "FAIL", "issecurevariable missing in this build", nil end
    local ok, isSecure, who
    if tbl == nil then
        ok, isSecure, who = pcall(issecurevariable, field)
    else
        ok, isSecure, who = pcall(issecurevariable, tbl, field)
    end
    if not ok then return "ERR", tostring(isSecure), nil end
    if isSecure then return "PASS", "secure", true end
    return "FAIL", "INSECURE — tainted by " .. tostring(who or "<unknown>"), false
end
ns.ReadTaint = readTaint

-- "LFGListFrame.SearchPanel.results" splits into the SearchPanel table and
-- "results"; a bare "SomeGlobal" resolves against _G. Both command forms —
-- one dotted path, or a path plus a separate field — land here.
local function resolveTarget(pathArg, fieldArg)
    if fieldArg then
        local tbl, failedAt = ResolvePath(pathArg)
        if type(tbl) ~= "table" then
            return nil, nil, format("%s did not resolve to a table (stopped at %s)",
                pathArg, tostring(failedAt or pathArg))
        end
        return tbl, fieldArg
    end

    local parent, field = pathArg:match("^(.*)%.([^%.]+)$")
    if not parent then
        -- No dot: a global, which issecurevariable takes by name.
        return nil, pathArg
    end
    local tbl, failedAt = ResolvePath(parent)
    if type(tbl) ~= "table" then
        return nil, nil, format("%s did not resolve to a table (stopped at %s)",
            parent, tostring(failedAt or parent))
    end
    return tbl, field
end

-------------------------------------------------------------------------------
-- /euidiag taint <path> [field]
-------------------------------------------------------------------------------
local function cmdOne(args)
    local pathArg = args[1]
    if not pathArg then
        emit("usage: /euidiag taint <Global> | <Frame.field> | <Frame> <field>")
        return
    end
    local tbl, field, err = resolveTarget(pathArg, args[2])
    if err then result("ERR", pathArg, err); return end

    local label = args[2] and (pathArg .. "." .. args[2]) or pathArg
    local tag, detail = readTaint(tbl, field)
    result(tag, label, detail)

    -- The value's own class matters as much as the taint: a field that is
    -- secure but holds a secret, and a field that is tainted but holds a plain
    -- number, are different problems with the same-looking symptom.
    local okValue, value = pcall(function()
        if tbl == nil then return _G[field] end
        return tbl[field]
    end)
    outf("  value = %s", okValue and ns.classify(value) or "unreadable")
end

-------------------------------------------------------------------------------
-- /euidiag taint scan <path> [depth]
-------------------------------------------------------------------------------
-- The generic form of "walk this frame and tell me what is dirty". Depth 1 is
-- almost always the right answer; deeper walks pull in UIParent and the whole
-- frame tree, so they are capped hard and de-duplicated by table identity.
local MAX_FIELDS = 400
local MAX_NODES = 60

local function scanTable(tbl, path, depth, visited, stats, out)
    if visited[tbl] or stats.nodes >= MAX_NODES then return end
    visited[tbl] = true
    stats.nodes = stats.nodes + 1

    local keys, walked = SortedKeys(tbl, MAX_FIELDS)
    if not walked then
        out[#out + 1] = { path = path, field = "*", detail = "table could not be iterated (secret?)" }
        return
    end

    for _, key in ipairs(keys) do
        local tag, detail, isSecure = readTaint(tbl, key)
        stats.fields = stats.fields + 1
        if isSecure == false then
            out[#out + 1] = { path = path, field = key, detail = detail }
            stats.insecure = stats.insecure + 1
        elseif isSecure == nil and tag == "ERR" then
            stats.unreadable = stats.unreadable + 1
        end

        if depth > 1 then
            local okChild, child = pcall(function() return tbl[key] end)
            -- Only recurse into plain tables. Frames carry references back up
            -- into the whole UI, and following them turns a scan of one panel
            -- into a scan of the interface.
            if okChild and type(child) == "table" and not visited[child]
                and not (child.GetObjectType and child.IsForbidden) then
                scanTable(child, path .. "." .. key, depth - 1, visited, stats, out)
            end
        end
    end
end

local function cmdScan(args)
    local path = args[1]
    if not path then
        emit("usage: /euidiag taint scan <Frame> [depth]")
        return
    end
    local depth = tonumber(args[2]) or 1
    if depth > 3 then depth = 3 end

    local tbl, failedAt = ResolvePath(path)
    if type(tbl) ~= "table" then
        result("ERR", "scan " .. path,
            format("not a table (stopped at %s) — is its addon loaded?", tostring(failedAt or path)))
        return
    end

    header(format("Taint scan: %s (depth %d)", path, depth))
    local stats = { fields = 0, insecure = 0, unreadable = 0, nodes = 0 }
    local out = {}
    scanTable(tbl, path, depth, {}, stats, out)

    if #out == 0 then
        result("PASS", path, format("%d field(s) across %d table(s), all secure", stats.fields, stats.nodes))
    else
        for _, entry in ipairs(out) do
            result("FAIL", entry.path .. "." .. entry.field, entry.detail)
        end
        result("INFO", "scan totals",
            format("%d insecure of %d field(s) across %d table(s)", stats.insecure, stats.fields, stats.nodes))
    end
    if stats.unreadable > 0 then
        outf("  %d field(s) could not be read", stats.unreadable)
    end
    if stats.nodes >= MAX_NODES then
        result("WARN", "scan", format("stopped at the %d-table cap — narrow the path or lower the depth", MAX_NODES))
    end
end

-------------------------------------------------------------------------------
-- /euidiag taint snap / diff
-------------------------------------------------------------------------------
-- Snapshot, go do the thing, diff. This is the reusable shape of every
-- "did that click taint anything?" experiment: the baseline is what makes an
-- INSECURE reading afterwards mean something, because taint is sticky and a
-- field that was already dirty when you started tells you nothing.
--
-- Kept in memory rather than SavedVariables on purpose: taint does not survive
-- a reload, so neither should a baseline taken before one.
local snapshots = {}

local function captureSnapshot(paths)
    local snap = { paths = paths, taken = GetTime(), fields = {}, tables = {} }
    for _, path in ipairs(paths) do
        local tbl = ResolvePath(path)
        if type(tbl) ~= "table" then
            snap.tables[path] = false
        else
            snap.tables[path] = true
            local keys = SortedKeys(tbl, MAX_FIELDS)
            for _, key in ipairs(keys) do
                local _, _, isSecure = readTaint(tbl, key)
                snap.fields[path .. "." .. key] = isSecure
            end
        end
    end
    return snap
end

local function cmdSnap(args)
    local label = args[1]
    if not label or not args[2] then
        emit("usage: /euidiag taint snap <label> <Frame> [Frame...]")
        emit("  then do the thing, then: /euidiag taint diff <label>")
        return
    end
    local paths = {}
    for i = 2, #args do paths[#paths + 1] = args[i] end

    local snap = captureSnapshot(paths)
    snapshots[label] = snap

    local counted, dirty = 0, 0
    for _, isSecure in pairs(snap.fields) do
        counted = counted + 1
        if isSecure == false then dirty = dirty + 1 end
    end
    header(format("Snapshot '%s'", label))
    for _, path in ipairs(paths) do
        result(snap.tables[path] and "INFO" or "SKIP", path,
            snap.tables[path] and "captured" or "did not resolve to a table")
    end
    outf("  %d field(s) recorded, %d already insecure", counted, dirty)
    if dirty > 0 then
        result("WARN", "baseline", "something is tainted already — /reload for a clean run")
    end
    emit("  now do the thing, then /euidiag taint diff " .. label)
end

local function cmdDiff(args)
    local label = args[1]
    local snap = label and snapshots[label]
    if not snap then
        result("ERR", "diff", "no snapshot named " .. tostring(label) .. " — /euidiag taint snaps")
        return
    end
    local after = captureSnapshot(snap.paths)

    header(format("Diff '%s' (%.1fs after the snapshot)", label, GetTime() - snap.taken))
    local flipped, appeared, vanished, healed = 0, 0, 0, 0

    local keys = {}
    for key in pairs(snap.fields) do keys[#keys + 1] = key end
    for key in pairs(after.fields) do
        if snap.fields[key] == nil then keys[#keys + 1] = key end
    end
    sort(keys)

    for _, key in ipairs(keys) do
        local before, now = snap.fields[key], after.fields[key]
        if before == nil and now ~= nil then
            appeared = appeared + 1
            result(now == false and "FAIL" or "INFO", key,
                now == false and "NEW and already insecure" or "new field, secure")
        elseif before ~= nil and now == nil then
            vanished = vanished + 1
            result("INFO", key, "field is gone")
        elseif before == true and now == false then
            flipped = flipped + 1
            -- Re-read for the name: the snapshot only stores the boolean.
            local parent, field = key:match("^(.*)%.([^%.]+)$")
            local tbl = parent and ResolvePath(parent)
            local _, detail = readTaint(type(tbl) == "table" and tbl or nil, field or key)
            result("FAIL", key, "WENT INSECURE — " .. tostring(detail))
        elseif before == false and now == true then
            -- Only possible if the field was rewritten by secure code since.
            healed = healed + 1
            result("INFO", key, "was insecure, now secure (rewritten by secure code)")
        end
    end

    if flipped == 0 and appeared == 0 and vanished == 0 and healed == 0 then
        result("PASS", "diff", "nothing changed — no field went insecure")
    else
        outf("  %d flipped to insecure, %d appeared, %d vanished, %d healed",
            flipped, appeared, vanished, healed)
    end
end

local function cmdSnaps(args)
    if args[1] == "drop" then
        local label = args[2]
        if not label or not snapshots[label] then
            result("ERR", "snaps drop", "no snapshot named " .. tostring(label))
            return
        end
        snapshots[label] = nil
        outf("dropped snapshot '%s'", label)
        return
    end
    local labels = {}
    for label in pairs(snapshots) do labels[#labels + 1] = label end
    sort(labels)
    if #labels == 0 then emit("no snapshots — /euidiag taint snap <label> <Frame>"); return end
    for _, label in ipairs(labels) do
        local snap = snapshots[label]
        local counted = 0
        for _ in pairs(snap.fields) do counted = counted + 1 end
        outf("  %s  %d field(s) over %s  (%.0fs ago)",
            Pad(label, 20), counted, concat(snap.paths, ", "), GetTime() - snap.taken)
    end
end

-------------------------------------------------------------------------------
-- /euidiag taint watch <path> <field>
-------------------------------------------------------------------------------
-- Polling beats a snapshot when you do not know what triggers the taint: it
-- names the moment, and GetTime() at the flip is usually enough to line it up
-- with whatever else you were doing.
local watches = {}
local watcher = CreateFrame("Frame")
watcher:Hide()

local WATCH_INTERVAL = 0.1
local watchElapsed = 0

watcher:SetScript("OnUpdate", function(self, delta)
    watchElapsed = watchElapsed + delta
    if watchElapsed < WATCH_INTERVAL then return end
    watchElapsed = 0

    local anyLeft = false
    for key, watch in pairs(watches) do
        local tbl = watch.parent and ResolvePath(watch.parent)
        local _, detail, isSecure = readTaint(type(tbl) == "table" and tbl or nil, watch.field)
        if isSecure == false then
            result("FAIL", "taint watch " .. key,
                format("went insecure %.1fs in — %s", GetTime() - watch.started, tostring(detail)))
            watches[key] = nil
        else
            anyLeft = true
        end
    end
    if not anyLeft then
        watcher:Hide()
        emit("taint watch: nothing left to watch")
    end
end)

local function cmdWatch(args)
    if (args[1] or ""):lower() == "stop" then
        wipe(watches)
        watcher:Hide()
        emit("taint watch: cleared")
        return
    end
    local path = args[1]
    if not path then
        emit("usage: /euidiag taint watch <Frame.field> | <Frame> <field> | stop")
        return
    end
    local tbl, field, err = resolveTarget(path, args[2])
    if err then result("ERR", "watch " .. path, err); return end

    -- Store the path, not the table: a panel can be rebuilt while we watch, and
    -- re-resolving each tick follows the live object instead of a stale one.
    local parent = args[2] and path or path:match("^(.*)%.[^%.]+$")
    local key = args[2] and (path .. "." .. args[2]) or path

    local _, detail, isSecure = readTaint(tbl, field)
    if isSecure == false then
        result("FAIL", "watch " .. key, "already insecure — " .. tostring(detail))
        emit("  /reload for a clean run, then arm the watch again")
        return
    end
    watches[key] = { parent = parent, field = field, started = GetTime() }
    watcher:Show()
    result("PASS", "watch " .. key, "armed and secure — go trigger it")
end

-------------------------------------------------------------------------------
-- /euidiag taint errors
-------------------------------------------------------------------------------
-- Capture is on from load. By the time a blocked action surprises you it is
-- already too late to have switched it on, and listening for two events that
-- almost never fire costs nothing.
local blocked = {}
local blockedOrder = {}
local captureOn = true
local liveEcho = true
local ECHO_COOLDOWN = 5

local function onBlocked(self, event, addon, func)
    if not captureOn then return end
    local key = format("%s|%s|%s", event, tostring(addon), tostring(func))
    local entry = blocked[key]
    if not entry then
        entry = { event = event, addon = addon, func = func, count = 0, first = GetTime() }
        blocked[key] = entry
        blockedOrder[#blockedOrder + 1] = key
    end
    entry.count = entry.count + 1
    entry.last = GetTime()

    -- One line per distinct action, then quiet: a blocked action inside an
    -- OnUpdate fires every frame, and echoing all of them buries the first one.
    if liveEcho and (not entry.echoed or (GetTime() - entry.echoed) > ECHO_COOLDOWN) then
        entry.echoed = GetTime()
        result(event == "ADDON_ACTION_FORBIDDEN" and "FAIL" or "WARN",
            event, format("%s tried %s", tostring(addon), tostring(func)))
    end
end

ns.addon:RegisterEvent("ADDON_ACTION_BLOCKED", onBlocked)
ns.addon:RegisterEvent("ADDON_ACTION_FORBIDDEN", onBlocked)

local function cmdErrors(args)
    local sub = (args[1] or ""):lower()
    if sub == "off" then
        captureOn = false
        emit("blocked-action capture off")
        return
    elseif sub == "on" then
        captureOn = true
        emit("blocked-action capture on")
        return
    elseif sub == "quiet" then
        liveEcho = not liveEcho
        outf("live echo %s (capture stays %s)", liveEcho and "on" or "off", captureOn and "on" or "off")
        return
    elseif sub == "clear" then
        wipe(blocked)
        wipe(blockedOrder)
        emit("blocked-action list cleared")
        return
    end

    header("Blocked / forbidden actions this session")
    if #blockedOrder == 0 then
        result("PASS", "capture", captureOn and "nothing blocked so far" or "capture is OFF")
        emit("  BLOCKED means tainted addon code touched a protected function.")
        emit("  FORBIDDEN means it called something addons may never call.")
        return
    end
    for _, key in ipairs(blockedOrder) do
        local entry = blocked[key]
        result(entry.event == "ADDON_ACTION_FORBIDDEN" and "FAIL" or "WARN",
            format("%s %s", Pad(entry.addon or "?", 28), tostring(entry.func)),
            format("x%d, first at %.0fs uptime", entry.count, entry.first))
    end
    outf("  %d distinct action(s); capture %s, live echo %s",
        #blockedOrder, captureOn and "on" or "off", liveEcho and "on" or "off")
    emit("  The named addon is the one holding the taint, which is not always")
    emit("  the one that created it — /euidiag taint log 2 for the full chain.")
end

-------------------------------------------------------------------------------
-- /euidiag taint log
-------------------------------------------------------------------------------
-- The client's own taint log is the only thing that shows the whole chain of
-- custody. It costs frame time and needs a reload either way, so it is a
-- deliberate switch rather than something this addon turns on for you.
local TAINT_LOG_LEVELS = {
    ["0"] = "off",
    ["1"] = "log taint (writes Logs/taint.log)",
    ["2"] = "verbose — every taint transfer, very slow",
}

local function cmdLog(args)
    if not GetCVar then result("FAIL", "taint log", "GetCVar missing"); return end
    local ok, current = pcall(GetCVar, "taintLog")
    if not ok or current == nil then
        result("FAIL", "taintLog", "the client does not expose this CVar")
        return
    end

    local wanted = args[1]
    if wanted == nil then
        outf("taintLog = %s (%s)", tostring(current), TAINT_LOG_LEVELS[tostring(current)] or "?")
        for level, meaning in pairs(TAINT_LOG_LEVELS) do
            outf("  %s = %s", level, meaning)
        end
        emit("  /euidiag taint log 1, then /reload — the log lands in")
        emit("  <WoW>/Logs/taint.log and is written as the session runs.")
        return
    end
    if not TAINT_LOG_LEVELS[wanted] then
        result("ERR", "taint log", "level must be 0, 1 or 2")
        return
    end
    local setOK, err = pcall(SetCVar, "taintLog", wanted)
    if not setOK then
        result("ERR", "taint log", tostring(err))
        return
    end
    result("PASS", "taintLog", format("set to %s (%s)", wanted, TAINT_LOG_LEVELS[wanted]))
    emit("  /reload for it to take effect. Level 2 costs real frame time —")
    emit("  set it back to 0 when you have what you need.")
end

-------------------------------------------------------------------------------
local SUBCOMMANDS = {
    scan   = cmdScan,
    snap   = cmdSnap,
    diff   = cmdDiff,
    snaps  = cmdSnaps,
    watch  = cmdWatch,
    errors = cmdErrors,
    log    = cmdLog,
}

ns.Command("taint", {
    group = "Taint",
    usage = "taint <path>|scan|snap|diff|watch",
    help  = "is it secure, who tainted it, when did it flip (also: snaps, errors, log)",
    fn    = function(args)
        local sub = (args[1] or ""):lower()
        local handler = SUBCOMMANDS[sub]
        if not handler then
            -- Not a subcommand, so it is a path: `/euidiag taint LFGListFrame`.
            return cmdOne(args)
        end
        local rest = {}
        for i = 2, #args do rest[i - 1] = args[i] end
        return handler(rest)
    end,
})
