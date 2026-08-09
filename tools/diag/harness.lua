-- Load the diagnostics addon under a stubbed WoW environment and drive it.
-- Catches load-order faults, nil calls during registration, and dispatch bugs
-- that a syntax check cannot see.

-- Resolve the addon folder relative to this file, so the harness travels with
-- the checkout instead of hardcoding one machine's install path.
--
-- Two layouts are accepted on purpose. The addon lives in wow-tools under
-- addons/, and it is ALSO symlinked into a WoW AddOns folder next to the rest
-- of the suite -- where a copy of this harness is a reasonable thing to drop
-- when reproducing something against the installed files rather than the repo.
local selfPath = debug.getinfo(1, "S").source:sub(2)
local here = selfPath:gsub("[^/]*$", "")
local DIR
for _, candidate in ipairs({
    here .. "../../addons/EllesmereUISecretsDiag/",  -- wow-tools: tools/diag/
    here .. "../../EllesmereUISecretsDiag/",         -- WoW AddOns: <addon>/.tools/diag/
    here .. "../EllesmereUISecretsDiag/",
}) do
    local probe = io.open(candidate .. "Core.lua", "r")
    if probe then
        probe:close()
        DIR = candidate
        break
    end
end
if not DIR then
    io.stderr:write("harness.lua: cannot find EllesmereUISecretsDiag/Core.lua near ", here, "\n")
    os.exit(1)
end

-- === frame stub ============================================================
local frames = {}
local function newFrame(kind)
    local f = { _scripts = {}, _kind = kind, _shown = true, _attrs = {} }
    function f:SetScript(which, fn) self._scripts[which] = fn end
    function f:GetScript(which) return self._scripts[which] end
    -- Show/Hide fire OnShow/OnHide the way the client does. Without that, any
    -- work a frame does on show is silently never exercised.
    function f:Show()
        local was = self._shown
        self._shown = true
        if not was and self._scripts.OnShow then self._scripts.OnShow(self) end
    end
    function f:Hide()
        local was = self._shown
        self._shown = false
        if was and self._scripts.OnHide then self._scripts.OnHide(self) end
    end
    function f:IsShown() return self._shown end
    function f:IsVisible() return self._shown end
    function f:RegisterEvent() end
    function f:UnregisterEvent() end
    function f:SetAttribute(k, v) self._attrs[k] = v end
    function f:GetAttribute(k) return self._attrs[k] end
    function f:CreateTexture() return newFrame("Texture") end
    function f:CreateFontString() return newFrame("FontString") end
    function f:GetObjectType() return self._kind end
    function f:SetText(t) self._text = t end
    function f:GetLeft() return 0 end
    function f:GetBottom() return 0 end
    function f:GetWidth() return self._w or 100 end
    function f:GetHeight() return self._h or 20 end
    function f:SetWidth(w) self._w = w end
    function f:SetHeight(h) self._h = h end
    function f:SetSize(w, h) self._w, self._h = w, h end
    function f:SetShown(s) if s then self:Show() else self:Hide() end end
    function f:CreateLine()
        local line = newFrame("Line")
        line._segments = true
        return line
    end
    function f:SetStartPoint(p, rel, x, y) self._start = { x, y } end
    function f:SetColorTexture(r, g, b, a) self._color = { r, g, b, a } end
    function f:SetEndPoint(p, rel, x, y) self._end = { x, y } end
    function f:GetPoint() return "CENTER", UIParent, "CENTER", 0, 0 end
    function f:SetTextColor(r, g, b) self._color = { r, g, b } end
    function f:ClearAllPoints() end
    function f:StopMovingOrSizing() end
    -- Anything else is a no-op that returns the frame, which covers the long
    -- tail of SetSize/SetPoint/SetMovable/... without listing them.
    setmetatable(f, { __index = function(t, k)
        -- Underscore keys are the stub's own data fields. Fabricating a method
        -- for those makes every unset field read back as a truthy function,
        -- which silently poisons anything that tests or formats them.
        if type(k) == "string" and k:sub(1, 1) == "_" then return nil end
        local fn = function(...) return nil end
        rawset(t, k, fn)
        return fn
    end })
    frames[#frames + 1] = f
    return f
end

function CreateFrame(kind, name, parent, template)
    local f = newFrame(kind)
    f.TitleText = newFrame("FontString")
    if name then _G[name] = f end
    return f
end

UIParent = newFrame("Frame")
UISpecialFrames = {}
ChatFontNormal, GameFontNormal = {}, {}

-- === game stubs ============================================================
local clock = 1000
function GetTime() clock = clock + 0.5 return clock end
function GetServerTime() return 1785000000 end
function GetFramerate() return 58.3 end
function GetBuildInfo() return "12.0.7", "61234", "Aug 1 2026", 120007 end
function GetRealmName() return "Ravencrest" end
function UnitFullName() return "Ellesmere", "Ravencrest" end
function InCombatLockdown() return false end
function IsInInstance() return false, "none" end
function IsInRaid() return false end
function IsInGroup() return false end
function GetNumGroupMembers() return 0 end
function UnitExists() return false end
function IsAddOnLoaded() return true end
function GetCVar(n) return n == "taintLog" and "0" or nil end
function SetCVar() return true end
function GetCVarBool() return true end
-- Counted, not just stubbed. UpdateAddOnMemoryUsage walks every installed
-- addon, so the recorder is supposed to call it on its own slow cycle rather
-- than once per sample; without a count that stays true only by inspection.
memoryWalks = 0
function UpdateAddOnMemoryUsage() memoryWalks = memoryWalks + 1 end
function GetAddOnMemoryUsage(n) return 900 + #tostring(n) * 7 end
function issecretvalue() return false end
function issecrettable() return false end
function hooksecurefunc() end
function wipe(t) for k in pairs(t) do t[k] = nil end return t end
loadstring = loadstring or load
date = os.date   -- WoW exposes os.date as a global

-- Two fields tainted, so the scan and diff paths exercise a real finding.
local TAINTED = { EUIDiagFakePanel = { results = true } }
function issecurevariable(a, b)
    local tbl, field = a, b
    if b == nil then tbl, field = nil, a end
    for owner, fields in pairs(TAINTED) do
        if (tbl == _G[owner] or (tbl == nil and owner == field)) and fields[field] then
            return false, "EllesmereUIQoL"
        end
    end
    return true, nil
end

Enum = { AddOnProfilerMetric = {
    SessionAverageTime = 0, RecentAverageTime = 1, EncounterAverageTime = 2,
    LastTime = 3, PeakTime = 4, CountTimeOver1Ms = 5, CountTimeOver5Ms = 6,
    CountTimeOver10Ms = 7, CountTimeOver50Ms = 8, CountTimeOver100Ms = 9,
    CountTimeOver500Ms = 10, CountTimeOver1000Ms = 11 } }

cpuScale = 1.0
cpuMode = "spread"
tickCounter = 0
local ROSTER = { "EllesmereUINameplates", "EllesmereUIUnitFrames", "EllesmereUIActionBars" }
C_AddOnProfiler = {
    IsEnabled = function() return true end,
    GetAddOnMetric = function(name, m)
        if m == 1 then
            if cpuMode == "quiet" then
                -- Live EllesmereUI numbers: every module a few hundredths of a
                -- millisecond, varying by roughly a third of that.
                local seed = (#name % 5) * 0.002
                return 0.018 + seed + ((tickCounter or 0) % 4) * 0.0015
            end
            if cpuMode == "band" then
                -- Every module on a high baseline, varying only slightly: the
                -- case a zero-based axis squashes into the top of the panel.
                return 1.0 + (#name % 3) * 0.02
            end
            local base = (name == "EllesmereUI") and 4.0 or 0.04
            return base * (cpuScale or 1.0)
        end
        if m == 4 then return 12.5 end
        if m == 7 then return (name == "EllesmereUINameplates") and 3 or 0 end
        return 0.2
    end,
    GetOverallMetric = function() return 2.4 end,
    GetApplicationMetric = function() return 16.6 end,
    GetTopKAddOnsForMetric = function(m, k)
        local out = {}
        for i, n in ipairs(ROSTER) do out[i] = { addOnName = n, metricValue = i * 0.3 } end
        return out
    end,
}
C_AddOns = {
    IsAddOnLoaded = function() return true end,
    GetNumAddOns = function() return #ROSTER + 1 end,
    GetAddOnInfo = function(i) return (i == 1) and "EllesmereUI" or ROSTER[i - 1] end,
}

EllesmereUI = { ADDON_ROSTER = {} }
for _, folder in ipairs(ROSTER) do
    table.insert(EllesmereUI.ADDON_ROSTER, { folder = folder, display = folder:gsub("^EllesmereUI", "") })
end

local eventFrames = {}
EllesmereUI.Lite = { NewAddon = function(name)
    local addon = { name = name, _handlers = {} }
    function addon:RegisterEvent(ev, cb) self._handlers[ev] = cb end
    function addon:UnregisterEvent(ev) self._handlers[ev] = nil end
    eventFrames[#eventFrames + 1] = addon
    return addon
end }

SlashCmdList = {}
local outputLines = 0
local realPrint = print
function print(...) outputLines = outputLines + 1 realPrint(...) end

-- A frame the taint tools can point at.
EUIDiagFakePanel = newFrame("Frame")
EUIDiagFakePanel.results = {}
EUIDiagFakePanel.searching = false
EUIDiagFakePanel.totalResults = 0

-- === load ==================================================================
local FILES = { "Core.lua", "Perf.lua", "Taint.lua", "Secrets.lua", "Investigations.lua" }
local ns = {}
for _, file in ipairs(FILES) do
    local chunk, err = loadfile(DIR .. file)
    if not chunk then error("load " .. file .. ": " .. tostring(err)) end
    local ok, runErr = pcall(chunk, "EllesmereUISecretsDiag", ns)
    if not ok then error("run " .. file .. ": " .. tostring(runErr)) end
    print(("== loaded %s"):format(file))
end

-- ADDON_LOADED, so SavedVariables come up.
for _, addon in ipairs(eventFrames) do
    local handler = addon._handlers["ADDON_LOADED"]
    if handler then handler(addon, "ADDON_LOADED", "EllesmereUISecretsDiag") end
end
assert(ns.db, "SavedVariables were never initialised")
print("== SavedVariables ready")

-- === drive =================================================================
local slash = SlashCmdList.EUIDIAG
assert(slash, "slash handler was never registered")

local SCRIPT = {
    "", "help", "help all",
    "cpu", "cpu spikes", "cpu all 3", "mem", "mem",
    "cpu window", "cpu window 0.25", "cpu window",
    "rec status", "rec start 0.5 20", "rec status",
    "taint EUIDiagFakePanel.results",
    "taint EUIDiagFakePanel searching",
    "taint scan EUIDiagFakePanel",
    "taint snap base EUIDiagFakePanel",
    "taint diff base",
    "taint snaps",
    "taint watch EUIDiagFakePanel searching",
    "taint watch stop",
    "taint errors", "taint log",
    "secrets list", "secrets list all",
    "eval 1+1", "eval return 'hello', {a=1,b=2}", "eval this is not lua",
    "save probe-run", "logs",
    "nosuchcommand",
}

local failures = {}
for _, line in ipairs(SCRIPT) do
    realPrint(("\n--- /euidiag %s"):format(line))
    local ok, err = pcall(slash, line)
    if not ok then
        failures[#failures + 1] = line .. "  ->  " .. tostring(err)
        realPrint("!!! ERROR: " .. tostring(err))
    end
end

-- Drive the recorder's OnUpdate so sampling actually runs.
local walksBefore = memoryWalks
for _, f in ipairs(frames) do
    local onUpdate = f._scripts and f._scripts["OnUpdate"]
    if onUpdate and f._shown then
        for _ = 1, 8 do pcall(onUpdate, f, 1.0) end
    end
end
-- Memory must NOT be read once per sample: UpdateAddOnMemoryUsage walks every
-- installed addon, and at a dungeon-rate interval that is the expensive part of
-- sampling. Eight samples at 0.5s fit inside a single MEMORY_EVERY window, so
-- the recorder should walk at most once across all of them.
local walksDuring = memoryWalks - walksBefore
realPrint(("  memory walks during 8 samples: %d"):format(walksDuring))
if walksDuring > 1 then
    failures[#failures + 1] =
        ("recorder walked addon memory %d times over 8 samples"):format(walksDuring)
end
realPrint("\n--- /euidiag rec stop")
local ok, err = pcall(slash, "rec stop")
if not ok then failures[#failures + 1] = "rec stop -> " .. tostring(err) end
for _, line in ipairs({ "rec list", "rec export" }) do
    realPrint(("\n--- /euidiag %s"):format(line))
    local ok2, err2 = pcall(slash, line)
    if not ok2 then failures[#failures + 1] = line .. " -> " .. tostring(err2) end
end


-- The monitor window: open it, tick its driver, and read the rendered cells
-- back out. An open window that never repaints is the failure this catches,
-- along with an average toggle that never diverges from live and a graph that
-- draws no line segments.
realPrint("\n--- monitor window repaint")
slash("cpu window")
local win = _G.EUIDiagCPUMonitor
assert(win, "monitor frame was never created")
assert(win:IsShown(), "first `cpu window` did not open the window")
for _, f in ipairs(frames) do
    local onUpdate = f._scripts and f._scripts["OnUpdate"]
    if onUpdate and f._shown and f ~= win then pcall(onUpdate, f, 2.0) end
end
local firstCell = win.rows[1].cells.display._text
local msCell = win.rows[1].cells.recent._text
local status = win.status._text
realPrint(("  row1: %s   ms=%s"):format(tostring(firstCell), tostring(msCell)))
realPrint(("  status: %s"):format(tostring(status)))
if not firstCell or firstCell == "" then
    failures[#failures + 1] = "monitor window opened but never painted a row"
end
if not status or status == "" then
    failures[#failures + 1] = "monitor status line stayed empty"
end

-- (a) average mode. Feed a changing series first, so a running mean that is
-- silently just echoing the live value shows up as an equal reading.
local function tickDriver(n)
    for _ = 1, (n or 1) do
        tickCounter = (tickCounter or 0) + 1
        for _, f in ipairs(frames) do
            local onUpdate = f._scripts and f._scripts["OnUpdate"]
            if onUpdate and f._shown and f ~= win then pcall(onUpdate, f, 2.0) end
        end
    end
end
-- A long-ago burst followed by a calm stretch. A rolling window must have
-- forgotten the burst by now; a cumulative mean never does. That difference is
-- the whole point of the change, so assert on it directly.
cpuScale = 8.0
tickDriver(6)
cpuScale = 1.0
tickDriver(30)
local liveText = win.rows[1].cells.recent._text

-- Cycle the whole ladder and record what each mode reports.
local seen = {}
for _ = 1, 5 do
    win.averageButton._scripts.OnClick(win.averageButton)
    seen[#seen + 1] = ("%s=%s"):format(
        tostring(win.averageButton._text), tostring(win.rows[1].cells.recent._text))
end
realPrint(("  live=%s"):format(tostring(liveText)))
realPrint(("  cycle: %s"):format(table.concat(seen, "  ")))
if win.averageButton._text ~= "Avg: off" then
    failures[#failures + 1] = "the Avg button did not cycle back round to off"
end

-- Land on the short rolling window and compare it against the session mean.
win.averageButton._scripts.OnClick(win.averageButton)   -- 10s
local rolling = tonumber(win.rows[1].cells.recent._text)
local rollingHeader = win.header.cells.recent._text
for _ = 1, 3 do win.averageButton._scripts.OnClick(win.averageButton) end  -- session
local session = tonumber(win.rows[1].cells.recent._text)
realPrint(("  rolling10s=%s (%s)  session=%s"):format(
    tostring(rolling), tostring(rollingHeader), tostring(session)))
if not rolling or not session then
    failures[#failures + 1] = "average modes did not produce numbers"
elseif rolling >= session then
    failures[#failures + 1] = "rolling average did not decay past the old burst "
        .. ("(rolling %s >= session %s)"):format(rolling, session)
end
if rollingHeader ~= "avg 10s" then
    failures[#failures + 1] = "rolling mode did not label its window in the header"
end
win.averageButton._scripts.OnClick(win.averageButton)   -- back to off

-- (b) the graph. Count line segments actually positioned.
local heightBefore = win:GetHeight()
win.plot:SetSize(500, 150)   -- the client derives this from the anchors
win.graphButton._scripts.OnClick(win.graphButton)
tickDriver(2)
local segments = 0
for _, line in ipairs(win.plot.lines) do
    if line._shown and line._start and line._end then segments = segments + 1 end
end
realPrint(("  graph: %d line segments, window %d -> %d tall, yAxis=%s span=%s"):format(
    segments, heightBefore, win:GetHeight(),
    tostring(win.plot.topLabel._text), tostring(win.plot.spanLabel._text)))
if segments == 0 then
    failures[#failures + 1] = "graph drew no line segments"
end
if win:GetHeight() <= heightBefore then
    failures[#failures + 1] = "graph did not grow the window"
end
if not win.plot:IsShown() then
    failures[#failures + 1] = "graph panel stayed hidden"
end

-- Click a row to pin it: the graph must then plot exactly the pinned set.
local before = 0
for _, line in ipairs(win.plot.lines) do
    if line._shown and line._start then before = before + 1 end
end
local pinnedName = win.rows[3].cells.display._text
win.rows[3].button._scripts.OnClick(win.rows[3].button)
tickDriver(1)
local pinnedSegments = 0
for _, line in ipairs(win.plot.lines) do
    if line._shown and line._start then pinnedSegments = pinnedSegments + 1 end
end
realPrint(("  pinned row3 (%s): series label=%s, segments %d -> %d"):format(
    tostring(pinnedName), tostring(win.seriesLabel._text), before, pinnedSegments))
if win.seriesLabel._text ~= "Series: 1" then
    failures[#failures + 1] = "pinning a row did not update the series label"
end
if pinnedSegments >= before then
    failures[#failures + 1] = "pinning one row did not reduce the plotted series"
end
if not win.rows[3].cells.display._text:find("^%*") then
    failures[#failures + 1] = "pinned row was not marked in the table"
end
-- All pins every module the monitor lists, so the label has to report that
-- count and the plot has to carry more series than the auto rule ever picks.
win.allButton._scripts.OnClick(win.allButton)
tickDriver(1)
local allSegments = 0
for _, line in ipairs(win.plot.lines) do
    if line._shown and line._start then allSegments = allSegments + 1 end
end
local moduleCount = #ns.EUIModules()
realPrint(("  All: series label=%s, %d modules, segments %d"):format(
    tostring(win.seriesLabel._text), moduleCount, allSegments))
if win.seriesLabel._text ~= ("Series: " .. moduleCount) then
    failures[#failures + 1] = "All did not pin every listed module"
end
if allSegments <= pinnedSegments then
    failures[#failures + 1] = "All did not add series to the plot"
end

-- None clears back to auto, which is NOT an empty plot: with nothing pinned
-- the auto rule picks the busiest few again.
win.noneButton._scripts.OnClick(win.noneButton)
tickDriver(1)
local autoSegments = 0
for _, line in ipairs(win.plot.lines) do
    if line._shown and line._start then autoSegments = autoSegments + 1 end
end
realPrint(("  None: series label=%s, segments back to %d"):format(
    tostring(win.seriesLabel._text), autoSegments))
if win.seriesLabel._text ~= "Series: auto" then
    failures[#failures + 1] = "None did not reset the series label to auto"
end
if autoSegments == 0 then
    failures[#failures + 1] = "None emptied the plot instead of falling back to auto"
end

-- Axis scaling. Zero-based squashes a set of series riding a high baseline
-- into the top of the panel; min-max zooms to the band they occupy. Assert on
-- the vertical spread of the drawn lines, not on the button label.
win.noneButton._scripts.OnClick(win.noneButton)   -- back to auto
cpuMode = "band"
win.resetButton._scripts.OnClick(win.resetButton)
tickDriver(20)

local function plottedSpread()
    local top, bottom = 0, math.huge
    for _, line in ipairs(win.plot.lines) do
        if line._shown and line._start then
            for _, point in ipairs({ line._start, line._end }) do
                if point then
                    if point[2] > top then top = point[2] end
                    if point[2] < bottom then bottom = point[2] end
                end
            end
        end
    end
    return top - bottom
end

local zeroSpread = plottedSpread()
local zeroBottomLabel = win.plot.bottomLabel._text
win.scaleButton._scripts.OnClick(win.scaleButton)
tickDriver(1)
local fitSpread = plottedSpread()

realPrint(("  scale button=%s  axis top=%s mid=%s bottom=%s"):format(
    tostring(win.scaleButton._text), tostring(win.plot.topLabel._text),
    tostring(win.plot.midLabel._text), tostring(win.plot.bottomLabel._text)))
realPrint(("  span=%s"):format(tostring(win.plot.spanLabel._text)))
realPrint(("  vertical spread: zero-based %.1f px -> min-max %.1f px (of %.0f)"):format(
    zeroSpread, fitSpread, win.plot:GetHeight()))

if win.scaleButton._text ~= "Scale: min-max" then
    failures[#failures + 1] = "scale button did not switch to min-max"
end
if tonumber((zeroBottomLabel:gsub(" ms", ""))) ~= 0 then
    failures[#failures + 1] = "zero-based axis floor was not zero: " .. tostring(zeroBottomLabel)
end
if not win.plot.topLabel._text:find("ms") or not win.plot.bottomLabel._text:find("ms") then
    failures[#failures + 1] = "axis labels are not in milliseconds"
end
if tonumber((win.plot.bottomLabel._text:gsub(" ms", ""))) == 0 then
    failures[#failures + 1] = "min-max axis still floored at zero"
end
if fitSpread <= zeroSpread * 2 then
    failures[#failures + 1] = ("min-max did not spread the band "
        .. "(zero-based %.1f px, min-max %.1f px)"):format(zeroSpread, fitSpread)
end

win.scaleButton._scripts.OnClick(win.scaleButton)
tickDriver(1)
if tonumber((win.plot.bottomLabel._text:gsub(" ms", ""))) ~= 0 then
    failures[#failures + 1] = "switching back to zero-based kept the fitted floor"
end
realPrint(("  back to zero-based: bottom=%s"):format(tostring(win.plot.bottomLabel._text)))
cpuMode = "spread"

-- Regression: five quiet modules around 0.02 ms. This produced a fixed
-- 0.00-0.05 ms axis with every line stuck in the lower half, because the
-- widening guard was an absolute 0.05 ms and its zero-clamp threw the fit away.
cpuMode = "quiet"
win.resetButton._scripts.OnClick(win.resetButton)
tickDriver(20)
win.scaleButton._scripts.OnClick(win.scaleButton)   -- into min-max
tickDriver(1)
assert(win.scaleButton._text == "Scale: min-max", "quiet check ran in the wrong mode")
local labels = { win.plot.topLabel._text, win.plot.midLabel._text, win.plot.bottomLabel._text }
local quietSpread = plottedSpread()
realPrint(("  quiet UI, min-max: axis %s / %s / %s, spread %.1f px of %.0f"):format(
    labels[1], labels[2], labels[3], quietSpread, win.plot:GetHeight()))
if labels[1] == labels[2] or labels[2] == labels[3] then
    failures[#failures + 1] = ("axis labels are indistinguishable at this scale: %s / %s / %s")
        :format(labels[1], labels[2], labels[3])
end
if tonumber((labels[3]:gsub(" ms", ""))) == 0 then
    failures[#failures + 1] = "quiet UI min-max axis collapsed back to a zero floor"
end
if quietSpread < win.plot:GetHeight() * 0.7 then
    failures[#failures + 1] = ("quiet series only spread %.1f px of %.0f in min-max")
        :format(quietSpread, win.plot:GetHeight())
end
realPrint(("  quiet UI table ms column: %s / %s / %s"):format(
    tostring(win.rows[1].cells.recent._text),
    tostring(win.rows[2].cells.recent._text),
    tostring(win.rows[3].cells.recent._text)))
for i = 1, 3 do
    local cell = win.rows[i].cells.recent._text
    if cell and tonumber(cell) == 0 then
        failures[#failures + 1] = "table ms column printed zero for a nonzero module: " .. cell
    end
end
win.scaleButton._scripts.OnClick(win.scaleButton)   -- back to zero-based
cpuMode = "spread"

-- Reset must clear the accumulated average back toward the live reading.
win.resetButton._scripts.OnClick(win.resetButton)
tickDriver(1)
realPrint(("  after reset: %s"):format(tostring(win.rows[1].cells.recent._text)))

slash("cpu window")
assert(not win:IsShown(), "second `cpu window` did not close the window")

-- Serialise SavedVariables the way the client does, so the offline analyzer is
-- fed exactly the shape the recorder produces.
local function serialize(v, indent)
    local pad = ("\t"):rep(indent)
    if type(v) == "table" then
        local out = { "{\n" }
        local arrayN = #v
        for i = 1, arrayN do
            out[#out + 1] = pad .. "\t" .. serialize(v[i], indent + 1) .. ", -- [" .. i .. "]\n"
        end
        for k, val in pairs(v) do
            if not (type(k) == "number" and k >= 1 and k <= arrayN) then
                out[#out + 1] = pad .. "\t[" .. serialize(k, indent + 1) .. "] = "
                    .. serialize(val, indent + 1) .. ",\n"
            end
        end
        out[#out + 1] = pad .. "}"
        return table.concat(out)
    elseif type(v) == "string" then
        return string.format("%q", v)
    end
    return tostring(v)
end
local svPath = "harness-sv.lua"
local fh = io.open(svPath, "w")
fh:write("\nEllesmereUISecretsDiagDB = " .. serialize(ns.db, 0) .. "\n")
fh:close()
realPrint("\nwrote " .. svPath .. " for the offline analyzer")

realPrint("\n============================================================")
if #failures == 0 then
    realPrint("ALL COMMANDS RAN CLEAN (" .. #SCRIPT + 5 .. " dispatches)")
else
    realPrint("FAILURES: " .. #failures)
    for _, f in ipairs(failures) do realPrint("  " .. f) end
    os.exit(1)
end
