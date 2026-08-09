-------------------------------------------------------------------------------
--  Core.lua  —  shared plumbing for the EllesmereUI diagnostics harness
--
--  Nothing in this file knows about a specific bug, module or patch. It gives
--  the tool files four things:
--
--    * a log buffer every tool writes through, exportable to a copy window or
--      to SavedVariables
--    * secret-safe value inspection (classify / isSecret / try / dumpFields)
--    * the /euidiag command registry, which builds its own help
--    * the SavedVariables root, handed to tools once it exists
--
--  Tools live in Perf.lua, Taint.lua, Secrets.lua and Investigations.lua and
--  register themselves against this file. Load order is set in the .toc; Core
--  must come first.
-------------------------------------------------------------------------------
local ADDON_NAME, ns = ...

local format, floor = string.format, math.floor
local concat, sort = table.concat, table.sort

ns.PREFIX = "|cff0cd29fEUI-Diag|r "

-- Created here, in this file's MAIN CHUNK, on purpose: the engine bills an
-- event handler's whole call tree to the addon whose execution context created
-- the frame carrying it, and Lite.NewAddon builds our event frame eagerly for
-- exactly that reason (see the attribution note in EllesmereUI_Lite.lua). A
-- diagnostics addon that mis-bills its own CPU to the parent would be lying in
-- the first table it prints.
ns.addon = EllesmereUI.Lite.NewAddon(ADDON_NAME)

-------------------------------------------------------------------------------
-- Log buffer
-------------------------------------------------------------------------------
-- Chat lines carry color; the buffer keeps the plain text, so an exported log
-- pastes into a bug report or a diff without escape codes in it.
local MAX_LINES = 20000
local logBuf = {}
ns.logBuf = logBuf

local function emit(plain, colored)
    logBuf[#logBuf + 1] = plain
    if #logBuf > MAX_LINES then
        -- Drop the oldest quarter rather than one line per write, so trimming
        -- costs one pass per few thousand lines instead of one per line.
        local drop = floor(MAX_LINES / 4)
        for i = 1, #logBuf - drop do logBuf[i] = logBuf[i + drop] end
        for i = #logBuf - drop + 1, #logBuf do logBuf[i] = nil end
        logBuf[1] = format("(... %d earlier lines dropped; log is capped at %d)", drop, MAX_LINES)
    end
    print(ns.PREFIX .. (colored or plain))
end
ns.emit = emit

local function outf(fmt, ...)
    local ok, s = pcall(format, fmt, ...)
    emit(ok and s or ("format error: " .. tostring(fmt)))
end
ns.outf = outf

local function header(name)
    emit("---- " .. name .. " ----", "|cff88ccff---- " .. name .. " ----|r")
end
ns.header = header

local TAG = {
    PASS   = { "[PASS] ",   "|cff40ff40[PASS]|r "   },
    FAIL   = { "[FAIL] ",   "|cffff4040[FAIL]|r "   },
    SECRET = { "[SECRET] ", "|cffff8800[SECRET]|r " },
    SKIP   = { "[skip] ",   "|cff888888[skip]|r "   },
    INFO   = { "[info] ",   "|cffaaaaaa[info]|r "   },
    ERR    = { "[ERR]  ",   "|cffff4040[ERR]|r  "   },
    WARN   = { "[warn] ",   "|cffffcc00[warn]|r "   },
}

local function result(tag, label, detail)
    local t = TAG[tag]
    local suffix = label .. (detail and (" — " .. detail) or "")
    if t then
        emit(t[1] .. suffix, t[2] .. suffix)
    else
        emit(tostring(tag) .. suffix)
    end
end
ns.result = result

-------------------------------------------------------------------------------
-- Secret-safe value inspection
-------------------------------------------------------------------------------
local function isSecret(v)
    if issecretvalue then
        local ok, r = pcall(issecretvalue, v)
        return ok and r or false
    end
    return false
end
ns.isSecret = isSecret

-- Human-readable description of any value. Never performs an operation on a
-- secret that would error, so this is safe to point at anything.
local function classify(v)
    if v == nil then return "nil" end
    if isSecret(v) then return "SECRET" end
    if issecrettable then
        local ok, r = pcall(issecrettable, v)
        if ok and r then return "SECRET-TABLE" end
    end
    local ok, s = pcall(function()
        local t = type(v)
        if t == "string" then
            if #v > 40 then return format("string:%q...", v:sub(1, 40)) end
            return format("string:%q", v)
        elseif t == "number" or t == "boolean" then
            return t .. ":" .. tostring(v)
        elseif t == "table" then
            return "table(#=" .. tostring(#v) .. ")"
        end
        return t
    end)
    return ok and s or "unclassifiable"
end
ns.classify = classify

-- pcall an API that may not exist. Returns ok, result-or-error.
local function try(fn, ...)
    if type(fn) ~= "function" then return false, "API missing" end
    return pcall(fn, ...)
end
ns.try = try

-- Dump a table's fields (one level), classifying each value.
local function dumpFields(label, tbl, indent, limit)
    indent = indent or "  "
    limit = limit or 25
    if type(tbl) ~= "table" then
        outf("%s%s = %s", indent, label, classify(tbl))
        return
    end
    outf("%s%s:", indent, label)
    local ok, err = pcall(function()
        local shown = 0
        for k, v in pairs(tbl) do
            if type(k) == "string" or type(k) == "number" then
                outf("%s  .%s = %s", indent, tostring(k), classify(v))
                shown = shown + 1
                if shown >= limit then
                    outf("%s  (... truncated)", indent)
                    break
                end
            end
        end
        if shown == 0 then outf("%s  (empty)", indent) end
    end)
    if not ok then outf("%s  iteration failed: %s", indent, tostring(err)) end
end
ns.dumpFields = dumpFields

-------------------------------------------------------------------------------
-- Path resolution
-------------------------------------------------------------------------------
-- "LFGListFrame.SearchPanel.ScrollBox" -> that table, or nil plus the segment
-- that stopped the walk. Every tool that takes a frame path on the command line
-- goes through here so they all fail the same way.
function ns.ResolvePath(path)
    if type(path) ~= "string" or path == "" then return nil, "(empty)" end
    local cur = _G
    local walked = ""
    for segment in path:gmatch("[^%.]+") do
        walked = (walked == "") and segment or (walked .. "." .. segment)
        if type(cur) ~= "table" then return nil, walked end
        local ok, nextValue = pcall(function() return cur[segment] end)
        if not ok or nextValue == nil then return nil, walked end
        cur = nextValue
    end
    return cur
end

-- Sorted key list of a table, string keys first. Used by anything that walks a
-- frame's fields and wants stable output across runs.
function ns.SortedKeys(tbl, limit)
    local keys = {}
    local ok = pcall(function()
        for k in pairs(tbl) do
            if type(k) == "string" then keys[#keys + 1] = k end
        end
    end)
    if not ok then return keys, false end
    sort(keys)
    if limit and #keys > limit then
        for i = #keys, limit + 1, -1 do keys[i] = nil end
    end
    return keys, true
end

-- Right-pad without string.format's %-Ns, which counts bytes and so misaligns
-- any column holding a UI escape code or a non-ASCII addon name.
function ns.Pad(s, width)
    s = tostring(s)
    local len = s:len()
    if len >= width then return s end
    return s .. (" "):rep(width - len)
end

-------------------------------------------------------------------------------
-- SavedVariables
-------------------------------------------------------------------------------
-- A plain global, not a Lite profile DB: recordings are diagnostic artifacts,
-- not user settings, and they must not travel with a profile export.
local dbCallbacks = {}

--- Run fn(db) once SavedVariables exist (immediately if they already do).
function ns.OnDB(fn)
    if ns.db then fn(ns.db) else dbCallbacks[#dbCallbacks + 1] = fn end
end

ns.addon:RegisterEvent("ADDON_LOADED", function(self, event, name)
    if name ~= ADDON_NAME then return end
    EllesmereUISecretsDiagDB = EllesmereUISecretsDiagDB or {}
    local db = EllesmereUISecretsDiagDB
    db.recordings = db.recordings or {}
    db.logs = db.logs or {}
    ns.db = db
    for _, fn in ipairs(dbCallbacks) do
        local ok, err = pcall(fn, db)
        if not ok then result("ERR", "OnDB callback", tostring(err)) end
    end
    wipe(dbCallbacks)
end)

-------------------------------------------------------------------------------
-- Export window
-------------------------------------------------------------------------------
local copyFrame
local function showExport(text, title)
    if not copyFrame then
        copyFrame = CreateFrame("Frame", "EUIDiagExport", UIParent, "BasicFrameTemplateWithInset")
        copyFrame:SetSize(760, 480)
        copyFrame:SetPoint("CENTER")
        copyFrame:SetMovable(true)
        copyFrame:EnableMouse(true)
        copyFrame:SetResizable(true)
        copyFrame:RegisterForDrag("LeftButton")
        copyFrame:SetScript("OnDragStart", copyFrame.StartMoving)
        copyFrame:SetScript("OnDragStop", copyFrame.StopMovingOrSizing)
        copyFrame:SetFrameStrata("DIALOG")

        local scroll = CreateFrame("ScrollFrame", nil, copyFrame, "UIPanelScrollFrameTemplate")
        scroll:SetPoint("TOPLEFT", 10, -28)
        scroll:SetPoint("BOTTOMRIGHT", -30, 8)

        local edit = CreateFrame("EditBox", nil, scroll)
        edit:SetMultiLine(true)
        edit:SetMaxLetters(0)
        edit:SetFontObject(ChatFontNormal)
        edit:SetWidth(700)
        edit:SetAutoFocus(false)
        edit:SetScript("OnEscapePressed", function(self)
            self:ClearFocus()
            copyFrame:Hide()
        end)
        -- Keep the text intact and selected: any keystroke that would modify it
        -- restores it and re-selects everything, so Ctrl+C always copies the
        -- whole export rather than whatever survived a stray keypress.
        edit:SetScript("OnTextChanged", function(self, userInput)
            if userInput then
                self:SetText(copyFrame.text or "")
                self:HighlightText()
            end
        end)
        edit:SetScript("OnEditFocusGained", function(self) self:HighlightText() end)
        -- Clicking inside the box drops the selection; grab it back on mouse-up.
        edit:SetScript("OnMouseUp", function(self) self:HighlightText() end)
        scroll:SetScrollChild(edit)
        copyFrame.edit = edit
    end
    copyFrame.TitleText:SetText(title or "EllesmereUI Diagnostics")
    copyFrame.text = text
    copyFrame.edit:SetText(text)
    copyFrame:Show()
    copyFrame.edit:SetFocus()
    copyFrame.edit:HighlightText()
end
ns.ShowExport = showExport

-------------------------------------------------------------------------------
-- Command registry
-------------------------------------------------------------------------------
-- Groups print in this order. A command with no group, or a group not listed
-- here, is hidden from the default help and only shows under `/euidiag help all`.
local GROUP_ORDER = { "Performance", "Taint", "Secrets", "Log" }

local commands = {}   -- name -> spec
local order = {}      -- registration order, for stable help output

--- Register a /euidiag subcommand.
--  spec.fn(args, rawArgs) receives arguments split on whitespace with their
--  case preserved — frame paths and addon names are case-sensitive, so the
--  dispatcher lowercases only the command word itself.
function ns.Command(name, spec)
    spec.name = name
    commands[name] = spec
    order[#order + 1] = name
end

function ns.RunCommand(name, args)
    local spec = commands[name]
    if not spec then return false end
    local ok, err = pcall(spec.fn, args or {}, concat(args or {}, " "))
    if not ok then result("ERR", "/euidiag " .. name, tostring(err)) end
    return true
end

local function showHelp(showAll)
    emit("EllesmereUI diagnostics — /euidiag <command>")
    local seen = {}
    local function printGroup(groupName)
        local lines = {}
        for _, name in ipairs(order) do
            local spec = commands[name]
            if spec.group == groupName and not seen[name] then
                seen[name] = true
                lines[#lines + 1] = format("  /euidiag %s  %s",
                    ns.Pad(spec.usage or name, 34), spec.help or "")
            end
        end
        if #lines > 0 then
            emit("|cffffd100" .. groupName .. "|r")
            for _, line in ipairs(lines) do emit(line) end
        end
    end
    for _, groupName in ipairs(GROUP_ORDER) do printGroup(groupName) end

    -- Anything left over is a parked investigation: still runnable, kept out of
    -- the way so the everyday tool reads as an everyday tool.
    local leftovers = {}
    for _, name in ipairs(order) do
        if not seen[name] then leftovers[#leftovers + 1] = name end
    end
    if #leftovers == 0 then return end
    if not showAll then
        -- The "(" keeps the count from abutting the color code: |c takes the
        -- next 8 hex digits, and a bare digit there reads like a broken escape.
        outf("|cff888888(%d parked investigation command(s) — /euidiag help all)|r", #leftovers)
        return
    end
    emit("|cffffd100Parked investigations|r (bespoke; kept for reference)")
    for _, name in ipairs(leftovers) do
        local spec = commands[name]
        outf("  /euidiag %s  %s", ns.Pad(spec.usage or name, 34), spec.help or "")
    end
end

ns.Command("help", {
    group = "Log",
    usage = "help [all]",
    help  = "this list; `all` includes parked investigations",
    fn    = function(args) showHelp(args[1] == "all") end,
})

ns.Command("copy", {
    group = "Log",
    usage = "copy",
    help  = "open the accumulated log in a copy-paste window",
    fn    = function()
        showExport(concat(logBuf, "\n"), "EllesmereUI Diagnostics — log")
    end,
})

ns.Command("clear", {
    group = "Log",
    usage = "clear",
    help  = "empty the log buffer",
    fn    = function()
        wipe(logBuf)
        emit("log cleared")
    end,
})

ns.Command("save", {
    group = "Log",
    usage = "save [label]",
    help  = "copy the log into SavedVariables (survives to disk on logout)",
    fn    = function(args)
        local db = ns.db
        if not db then result("ERR", "save", "SavedVariables not loaded yet"); return end
        local label = args[1] or date("%Y-%m-%d_%H-%M-%S")
        db.logs[label] = concat(logBuf, "\n")
        outf("saved %d lines as '%s' — reachable on disk after /reload or logout at", #logBuf, label)
        emit("  WTF/Account/<account>/SavedVariables/" .. ADDON_NAME .. ".lua")
    end,
})

ns.Command("logs", {
    group = "Log",
    usage = "logs [drop <label>]",
    help  = "list saved logs, or delete one",
    fn    = function(args)
        local db = ns.db
        if not db then result("ERR", "logs", "SavedVariables not loaded yet"); return end
        if args[1] == "drop" then
            local label = args[2]
            if not label or db.logs[label] == nil then
                result("ERR", "logs drop", "no saved log named " .. tostring(label))
                return
            end
            db.logs[label] = nil
            outf("dropped saved log '%s'", label)
            return
        end
        local labels = {}
        for label in pairs(db.logs) do labels[#labels + 1] = label end
        sort(labels)
        if #labels == 0 then emit("no saved logs"); return end
        for _, label in ipairs(labels) do
            outf("  %s  (%d bytes)", ns.Pad(label, 28), #db.logs[label])
        end
    end,
})

-------------------------------------------------------------------------------
-- Slash dispatch
-------------------------------------------------------------------------------
SLASH_EUIDIAG1 = "/euidiag"
SLASH_EUIDIAG2 = "/euisecdiag"
SlashCmdList.EUIDIAG = function(msg)
    -- Case is preserved: `/euidiag taint LFGListFrame results` has to reach the
    -- global under its real name, and addon folder names are case-sensitive too.
    -- Only the command word is normalised.
    local args = {}
    for word in tostring(msg or ""):gmatch("%S+") do args[#args + 1] = word end
    local cmd = (args[1] or ""):lower()
    for i = 1, #args do args[i] = args[i + 1] end

    if cmd == "" then showHelp(false); return end
    if not ns.RunCommand(cmd, args) then
        outf("unknown command '%s' — /euidiag help", cmd)
    end
end
