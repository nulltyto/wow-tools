-------------------------------------------------------------------------------
--  Perf.lua  —  where the frame time and the memory are going
--
--    /euidiag cpu            per-module CPU cost, right now
--    /euidiag cpu window     a window that keeps updating: rolling averages,
--                            a plot of the last minute, click a row to pin it
--                            (also: window <seconds|avg|graph|scale|reset>)
--    /euidiag cpu all [n]    the same for every loaded addon, not just ours
--    /euidiag cpu spikes     how often each module blew past 1/5/10/50/100ms
--    /euidiag mem            memory per module, with the delta since last call
--    /euidiag rec ...        sample all of the above on a timer into
--                            SavedVariables, for graphs and statistics.
--                            One sample a second by default, which is meant to
--                            hold a whole dungeon. Drop what you have finished
--                            with: the file is re-read at EVERY login.
--
--  All readings come from C_AddOnProfiler, which the client keeps running on
--  its own. The older GetAddOnCPUUsage path needs the scriptProfile CVar set
--  and a reload, and it double-counts anything a module calls in a Blizzard
--  frame, so it is not used here.
--
--  Attribution caveat worth remembering before believing any row: the engine
--  bills a script handler's entire call tree to the addon whose execution
--  context created the frame carrying the handler, NOT to the file the code
--  lives in. A module that hands work to a frame the parent created will read
--  low here while the parent reads high. EllesmereUI_Ticker.lua documents the
--  rule and the two ways to stay on the right side of it.
-------------------------------------------------------------------------------
local ADDON_NAME, ns = ...

local format, floor, sort, concat = string.format, math.floor, table.sort, table.concat
local emit, outf, header, result = ns.emit, ns.outf, ns.header, ns.result
local Pad = ns.Pad

local Metric = Enum and Enum.AddOnProfilerMetric

-- Cumulative "how many times did this module take longer than X" counters.
-- The high buckets are the ones that correspond to a visible hitch.
local SPIKE_BUCKETS = Metric and {
    { "1ms",   Metric.CountTimeOver1Ms    },
    { "5ms",   Metric.CountTimeOver5Ms    },
    { "10ms",  Metric.CountTimeOver10Ms   },
    { "50ms",  Metric.CountTimeOver50Ms   },
    { "100ms", Metric.CountTimeOver100Ms  },
    { "500ms", Metric.CountTimeOver500Ms  },
    { "1s",    Metric.CountTimeOver1000Ms },
} or {}

-------------------------------------------------------------------------------
-- Availability & raw readings
-------------------------------------------------------------------------------
local function profilerReady()
    if not (C_AddOnProfiler and C_AddOnProfiler.GetAddOnMetric and Metric) then
        result("FAIL", "C_AddOnProfiler", "not available in this build")
        return false
    end
    -- IsEnabled ships in the live client but is absent from the 12.0.5 doc
    -- export, so treat a missing function as "assume on" rather than as off.
    if C_AddOnProfiler.IsEnabled then
        local ok, enabled = pcall(C_AddOnProfiler.IsEnabled)
        if ok and enabled == false then
            result("FAIL", "C_AddOnProfiler", "profiling is disabled by the client")
            return false
        end
    end
    return true
end

local function metric(addon, which)
    local ok, v = pcall(C_AddOnProfiler.GetAddOnMetric, addon, which)
    return (ok and type(v) == "number") and v or 0
end

local function overallMetric(which)
    local ok, v = pcall(C_AddOnProfiler.GetOverallMetric, which)
    return (ok and type(v) == "number") and v or 0
end

local function appMetric(which)
    local ok, v = pcall(C_AddOnProfiler.GetApplicationMetric, which)
    return (ok and type(v) == "number") and v or 0
end

-- Blizzard's own share-of-frame formula (Blizzard_AddOnList/AddonList.lua):
-- measure the addon against a frame that contains it but not the other addons,
-- so one heavy addon cannot deflate everybody else's percentage.
local function sharePercent(addonMs, appMs, allAddonMs)
    local relative = appMs - allAddonMs + addonMs
    if relative <= 0 then return 0 end
    return addonMs / relative * 100
end

-------------------------------------------------------------------------------
-- Which addons count as "ours"
-------------------------------------------------------------------------------
local function isLoaded(name)
    if C_AddOns and C_AddOns.IsAddOnLoaded then
        local ok, loaded = pcall(C_AddOns.IsAddOnLoaded, name)
        return ok and loaded or false
    end
    return false
end

-- The parent first, then the roster in its authored order, then any other
-- loaded EllesmereUI* folder. The third pass is what keeps this honest as
-- modules are added: a new folder shows up without anyone editing a list here.
--
-- Cached on a short timer because the live monitor asks once a second and the
-- full walk touches every installed addon. A module that loads on demand shows
-- up within the TTL, which is soon enough for a list that changes at most once
-- or twice a session.
local MODULE_CACHE_TTL = 10
local moduleCache, moduleCacheAt

local function euiModules()
    if moduleCache and moduleCacheAt and (GetTime() - moduleCacheAt) < MODULE_CACHE_TTL then
        return moduleCache
    end
    local list, seen = {}, {}
    local function add(folder, display)
        if seen[folder] or not isLoaded(folder) then return end
        seen[folder] = true
        list[#list + 1] = { folder = folder, display = display or folder }
    end

    add("EllesmereUI", "Core (parent)")
    if EllesmereUI and EllesmereUI.ADDON_ROSTER then
        for _, info in ipairs(EllesmereUI.ADDON_ROSTER) do
            add(info.folder, info.display)
        end
    end
    if C_AddOns and C_AddOns.GetNumAddOns and C_AddOns.GetAddOnInfo then
        for i = 1, C_AddOns.GetNumAddOns() do
            local ok, name = pcall(C_AddOns.GetAddOnInfo, i)
            if ok and type(name) == "string" and name:find("^EllesmereUI") then
                add(name)
            end
        end
    end
    moduleCache, moduleCacheAt = list, GetTime()
    return list
end
ns.EUIModules = euiModules

-------------------------------------------------------------------------------
-- Memory
-------------------------------------------------------------------------------
-- UpdateAddOnMemoryUsage walks every addon, so it is only called when a memory
-- number is actually about to be printed or recorded.
local function refreshMemory()
    if UpdateAddOnMemoryUsage then pcall(UpdateAddOnMemoryUsage) end
end

local function memoryKB(folder)
    if not GetAddOnMemoryUsage then return 0 end
    local ok, kb = pcall(GetAddOnMemoryUsage, folder)
    return (ok and type(kb) == "number") and kb or 0
end

-- Roughly three significant figures, whatever the magnitude. A fixed "%.3f"
-- prints 0.000 for every module in a UI that costs hundredths of a
-- millisecond, which is most of them once the suite is behaving.
local function decimalsFor(magnitude)
    if magnitude >= 100 then return 0 end
    if magnitude >= 10 then return 1 end
    if magnitude >= 1 then return 2 end
    if magnitude >= 0.1 then return 3 end
    if magnitude >= 0.01 then return 4 end
    if magnitude >= 0.001 then return 5 end
    return 6
end

local function msText(value)
    if value == 0 then return "0" end
    return format("%." .. decimalsFor(math.abs(value)) .. "f", value)
end

local function formatKB(kb)
    if kb >= 1024 then return format("%.2f MB", kb / 1024) end
    return format("%.0f KB", kb)
end

-------------------------------------------------------------------------------
-- /euidiag cpu
-------------------------------------------------------------------------------
local function cpuTable(rows, appMs, allAddonMs, title)
    header(title)
    outf("  %s %s %s %s %s %s",
        Pad("MODULE", 26), Pad("RECENT", 12), Pad("SHARE", 8),
        Pad("SESSION", 12), Pad("PEAK", 12), "MEMORY")

    local totalRecent, totalSession, totalKB = 0, 0, 0
    for _, row in ipairs(rows) do
        totalRecent = totalRecent + row.recent
        totalSession = totalSession + row.session
        totalKB = totalKB + row.kb
        outf("  %s %s %s %s %s %s",
            Pad(row.display, 26),
            Pad(msText(row.recent) .. " ms", 12),
            Pad(format("%.2f%%", sharePercent(row.recent, appMs, allAddonMs)), 8),
            Pad(msText(row.session) .. " ms", 12),
            Pad(msText(row.peak) .. " ms", 12),
            formatKB(row.kb))
    end

    emit("  " .. ("-"):rep(78))
    outf("  %s %s %s %s %s %s",
        Pad(format("TOTAL (%d)", #rows), 26),
        Pad(msText(totalRecent) .. " ms", 12),
        Pad(format("%.2f%%", sharePercent(totalRecent, appMs, allAddonMs)), 8),
        Pad(msText(totalSession) .. " ms", 12),
        Pad("", 12),
        formatKB(totalKB))
    return totalRecent
end

local function contextLine(appMs, allAddonMs)
    local fps = GetFramerate and GetFramerate() or 0
    outf("  frame %.2f ms (%.0f fps) | all addons %s ms (%.2f%% of frame) | app %s ms",
        fps > 0 and (1000 / fps) or 0, fps,
        msText(allAddonMs), appMs > 0 and (allAddonMs / appMs * 100) or 0, msText(appMs))
    emit("  RECENT is the client's rolling per-frame average; SHARE is that")
    emit("  measured against a frame with the other addons removed.")
end

local function cmdCPU(args)
    if not profilerReady() then return end
    local sub = (args[1] or ""):lower()

    if sub == "spikes" then
        return ns.PerfSpikes(args)
    elseif sub == "window" or sub == "monitor" then
        return ns.PerfWindow(args)
    end

    local appMs = appMetric(Metric.RecentAverageTime)
    local allAddonMs = overallMetric(Metric.RecentAverageTime)
    refreshMemory()

    local rows = {}
    if sub == "all" then
        -- GetTopKAddOnsForMetric is the only way to enumerate by cost without
        -- reading every addon; ask for more than we print so ties settle.
        local n = tonumber(args[2]) or 20
        local ok, top = pcall(C_AddOnProfiler.GetTopKAddOnsForMetric, Metric.RecentAverageTime, n)
        if not ok or type(top) ~= "table" then
            result("ERR", "GetTopKAddOnsForMetric", tostring(top))
            return
        end
        for _, entry in ipairs(top) do
            local name = entry.addOnName
            rows[#rows + 1] = {
                display = name,
                recent  = entry.metricValue or 0,
                session = metric(name, Metric.SessionAverageTime),
                peak    = metric(name, Metric.PeakTime),
                kb      = memoryKB(name),
            }
        end
        cpuTable(rows, appMs, allAddonMs, format("Top %d addons by recent CPU", #rows))
    else
        for _, mod in ipairs(euiModules()) do
            rows[#rows + 1] = {
                display = mod.display,
                recent  = metric(mod.folder, Metric.RecentAverageTime),
                session = metric(mod.folder, Metric.SessionAverageTime),
                peak    = metric(mod.folder, Metric.PeakTime),
                kb      = memoryKB(mod.folder),
            }
        end
        sort(rows, function(a, b) return a.recent > b.recent end)
        cpuTable(rows, appMs, allAddonMs, "EllesmereUI modules by CPU")
    end
    contextLine(appMs, allAddonMs)
end

-------------------------------------------------------------------------------
-- /euidiag cpu spikes
-------------------------------------------------------------------------------
-- Averages hide hitches: a module that costs 0.02 ms on average and 400 ms once
-- a minute reads as free in the table above and feels terrible to play. These
-- are session-cumulative counts, so they only ever go up.
function ns.PerfSpikes(args)
    if not profilerReady() then return end
    if #SPIKE_BUCKETS == 0 then
        result("FAIL", "spike buckets", "Enum.AddOnProfilerMetric has no CountTimeOver* members")
        return
    end
    header("Spike counts per module (cumulative, this session)")

    local line = "  " .. Pad("MODULE", 26)
    for _, bucket in ipairs(SPIKE_BUCKETS) do line = line .. Pad(">" .. bucket[1], 8) end
    emit(line)

    local rows = {}
    for _, mod in ipairs(euiModules()) do
        local counts, worst = {}, 0
        for i, bucket in ipairs(SPIKE_BUCKETS) do
            counts[i] = metric(mod.folder, bucket[2])
            -- Rank by the most severe bucket that ever fired, not by raw count:
            -- one 500 ms stall matters more than a thousand 1 ms frames.
            if counts[i] > 0 then worst = i end
        end
        rows[#rows + 1] = { display = mod.display, counts = counts, worst = worst }
    end
    sort(rows, function(a, b)
        if a.worst ~= b.worst then return a.worst > b.worst end
        return (a.counts[1] or 0) > (b.counts[1] or 0)
    end)

    local anySpike = false
    for _, row in ipairs(rows) do
        if row.worst > 0 then
            anySpike = true
            local text = "  " .. Pad(row.display, 26)
            for i = 1, #SPIKE_BUCKETS do
                text = text .. Pad(row.counts[i] > 0 and tostring(row.counts[i]) or "-", 8)
            end
            emit(text)
        end
    end
    if not anySpike then
        result("PASS", "spikes", "no module has exceeded 1 ms in a single frame this session")
    end
end

-------------------------------------------------------------------------------
-- /euidiag mem
-------------------------------------------------------------------------------
local lastMem = {}

local function cmdMem(args)
    if not GetAddOnMemoryUsage then
        result("FAIL", "GetAddOnMemoryUsage", "API missing")
        return
    end
    refreshMemory()
    header("EllesmereUI module memory")
    outf("  %s %s %s", Pad("MODULE", 26), Pad("IN USE", 14), "SINCE LAST /euidiag mem")

    local rows, total = {}, 0
    for _, mod in ipairs(euiModules()) do
        local kb = memoryKB(mod.folder)
        total = total + kb
        rows[#rows + 1] = { display = mod.display, folder = mod.folder, kb = kb }
    end
    sort(rows, function(a, b) return a.kb > b.kb end)

    for _, row in ipairs(rows) do
        local previous = lastMem[row.folder]
        local delta = previous and (row.kb - previous) or nil
        outf("  %s %s %s",
            Pad(row.display, 26), Pad(formatKB(row.kb), 14),
            delta and format("%+.0f KB", delta) or "(first reading)")
        lastMem[row.folder] = row.kb
    end
    emit("  " .. ("-"):rep(60))
    outf("  %s %s", Pad(format("TOTAL (%d)", #rows), 26), formatKB(total))
    emit("  A steadily climbing delta across several readings is a leak; a")
    emit("  sawtooth is just Lua garbage that has not been collected yet.")
end

-------------------------------------------------------------------------------
-- Live monitor window
-------------------------------------------------------------------------------
-- The same numbers as `/euidiag cpu`, re-read on a timer, so you can watch a
-- module react to something instead of running the command before and after.
--
-- Three things the static table cannot show:
--   * the hitch column is a cumulative counter, so it visibly ticks up the
--     moment a module blows a frame
--   * the Avg button cycles rolling smoothing windows, because a single
--     reading swings too wildly to compare two modules by eye
--   * the graph plots the last minute, which is how you tell a module that is
--     steadily expensive from one that spikes on an event, and the Scale
--     button zooms the axis to the band the data occupies when everything is
--     riding a high flat baseline
local MONITOR_INTERVAL = 1
local MEMORY_INTERVAL = 5   -- UpdateAddOnMemoryUsage walks every addon
local MONITOR_MAX_ROWS = 24
local ROW_HEIGHT = 14

local PLOT_POINTS = 60      -- one minute at the default interval
local PLOT_SERIES = 6       -- more lines than this is unreadable, not more useful
local GRAPH_HEIGHT = 150
-- Only guards the degenerate all-zero plot. Anything larger starts
-- dictating the axis for a UI that legitimately costs very little.
local PLOT_FLOOR = 0.01     -- ms

-- Deeper than the plot needs, so a long averaging window still has samples to
-- average when the interval is short. 300 numbers per module is nothing.
local RING_SIZE = 300

-- Smoothing windows the Avg button cycles through. A rolling window is the
-- useful shape: a cumulative mean converges and stops responding, so after a
-- few minutes a real spike barely moves it. Rolling stays live but readable,
-- and the seconds are on the button so the number is never ambiguous.
--
-- `session` keeps the old cumulative behavior, which is still the right answer
-- for "what did this module cost over the pull I just did".
local AVERAGE_MODES = {
    { label = "off",     header = "ms",      status = "live:" },
    { label = "10s",     header = "avg 10s", status = "avg 10s:", seconds = 10 },
    { label = "30s",     header = "avg 30s", status = "avg 30s:", seconds = 30 },
    { label = "60s",     header = "avg 60s", status = "avg 60s:", seconds = 60 },
    { label = "session", header = "avg all", status = "avg since open",  session = true },
}

-- Distinguishable at small sizes and on both the dark inset and a light
-- background.
local PALETTE = {
    { 0.40, 0.80, 1.00 }, { 1.00, 0.60, 0.30 }, { 0.55, 0.90, 0.45 },
    { 1.00, 0.45, 0.55 }, { 0.80, 0.65, 1.00 }, { 1.00, 0.90, 0.40 },
    { 0.50, 1.00, 0.85 }, { 1.00, 0.75, 0.90 },
}

-- Click a row to pin or unpin its module. While anything is pinned the graph
-- plots exactly that set; with nothing pinned it falls back to the busiest
-- PLOT_SERIES rows. Colors are held per module rather than per sort position,
-- so a line keeps its color when the row it came from moves.
local plotSelection = {}
local plotColorOf = {}

local function colorFor(folder)
    local index = plotColorOf[folder]
    if index then return PALETTE[index] end
    local taken = {}
    for _, used in pairs(plotColorOf) do taken[used] = true end
    for candidate = 1, #PALETTE do
        if not taken[candidate] then
            plotColorOf[folder] = candidate
            return PALETTE[candidate]
        end
    end
    -- More series than colors: wrap rather than refuse to plot.
    plotColorOf[folder] = 1
    return PALETTE[1]
end

local function selectionCount()
    local n = 0
    for _ in pairs(plotSelection) do n = n + 1 end
    return n
end

-- x offset, width, justify. Real per-column FontStrings rather than one padded
-- line: the game's fonts are proportional, so space-padding does not line up.
-- The ms and peak columns are sized for the widest reading the significant
-- figure formatter produces ("0.000200"), not for the "0.000" it used to print.
local COLUMNS = {
    { key = "display", x = 12,  w = 246, justify = "LEFT",  title = "MODULE" },
    { key = "recent",  x = 262, w = 74,  justify = "RIGHT", title = "ms"     },
    { key = "share",   x = 340, w = 54,  justify = "RIGHT", title = "share"  },
    { key = "peak",    x = 398, w = 74,  justify = "RIGHT", title = "peak"   },
    { key = "hitches", x = 476, w = 48,  justify = "RIGHT", title = ">10ms"  },
    { key = "kb",      x = 528, w = 76,  justify = "RIGHT", title = "memory" },
}
-- Wide enough for the control row, which is what sets it: the seven controls
-- and their gaps come to 604, and the table columns above are spread to match
-- rather than left to end early against empty space.
local MONITOR_WIDTH = 622
local TABLE_BOTTOM = 28 + (MONITOR_MAX_ROWS + 1) * ROW_HEIGHT + 6
local CONTROLS_HEIGHT = 54
local BASE_HEIGHT = TABLE_BOTTOM + CONTROLS_HEIGHT

local monitor          -- the window, built on first use
local monitorElapsed = 0
local memoryElapsed = MEMORY_INTERVAL
local monitorInterval = MONITOR_INTERVAL
local averageIndex = 1   -- index into AVERAGE_MODES; 1 is live
local graphShown = false

-- Both modes share one axis across every plotted series; what changes is where
-- the axis starts.
--
--   0-max     bottom is zero. Heights are proportional to cost, so twice as
--             tall means twice as expensive.
--   min-max   bottom is the lowest value on screen, so the axis zooms to the
--             band the data actually occupies. Series sitting on a high flat
--             baseline show their variation instead of three near-flat lines.
--
-- Either way the axis is labelled in milliseconds. An earlier version scaled
-- each series to its own peak and labelled the axis 0-100%, which read like a
-- CPU percentage and made heights incomparable between lines.
local fitToRange = false

-- Created in the main chunk for the same reason the sampler is: the engine
-- bills a handler's call tree to whoever created the frame carrying it, and a
-- monitor that hid its own cost inside Blizzard's chat-command context would
-- be misreporting the one number it exists to report.
local monitorDriver = CreateFrame("Frame")
monitorDriver:Hide()

-------------------------------------------------------------------------------
-- Sample history
-------------------------------------------------------------------------------
-- One store per module feeds both features: a running sum for the average, and
-- a ring of the last PLOT_POINTS readings for the graph. The sum is kept
-- separately rather than derived from the ring so the average covers the whole
-- observation window, not just the last minute of it.
local history = {}
local historyStarted

local function pushSample(folder, value)
    local h = history[folder]
    if not h then
        h = { sum = 0, n = 0, ring = {}, head = 0 }
        history[folder] = h
    end
    h.sum = h.sum + value
    h.n = h.n + 1
    h.head = h.head % RING_SIZE + 1
    h.ring[h.head] = value
end

-- Oldest to newest, so the plot reads left to right. maxCount limits how far
-- back to look; the plot asks for its own span, the rolling average for its.
local function ringValues(folder, out, maxCount)
    wipe(out)
    local h = history[folder]
    if not h or h.n == 0 then return out end
    local count = math.min(h.n, maxCount or RING_SIZE, RING_SIZE)
    for i = 1, count do
        out[i] = h.ring[(h.head - count + i - 1) % RING_SIZE + 1]
    end
    return out
end

-- How many samples cover the requested window at the current interval. The
-- interval is adjustable, so this cannot be precomputed.
local function samplesForSeconds(seconds)
    return math.max(1, math.min(RING_SIZE, floor(seconds / monitorInterval + 0.5)))
end

-- Mean of the last `count` samples, or of everything seen when mode.session.
-- Summing directly rather than keeping an incremental window sum: `count`
-- moves with the interval, and a few thousand additions a second is far
-- cheaper than the bookkeeping to avoid them.
local function averageOf(folder, mode)
    local h = history[folder]
    if not h or h.n == 0 then return 0 end
    if mode.session then return h.sum / h.n end

    local count = math.min(h.n, samplesForSeconds(mode.seconds))
    local total = 0
    for i = 1, count do
        total = total + (h.ring[(h.head - count + i - 1) % RING_SIZE + 1] or 0)
    end
    return total / count
end

local function resetHistory()
    wipe(history)
    historyStarted = GetTime()
end

-------------------------------------------------------------------------------
-- Graph
-------------------------------------------------------------------------------
local plotScratch = {}

-- Axis labels take their precision from the RANGE, not from each value, so all
-- three read at the same scale and cannot collapse into the same string.
local function axisLabeller(yMin, yMax)
    local decimals = decimalsFor(yMax - yMin)
    return function(value) return format("%." .. decimals .. "f ms", value) end
end

local function plotLine(plot, index)
    local line = plot.lines[index]
    if not line then
        line = plot:CreateLine(nil, "ARTWORK")
        line:SetThickness(1.5)
        plot.lines[index] = line
    end
    return line
end

local function updateGraph(plot, rows)
    if not graphShown or not plot then return end

    local width, height = plot:GetWidth(), plot:GetHeight()
    if not width or width < 10 or not height or height < 10 then return end

    -- Pinned rows if there are any, otherwise the busiest few.
    local chosen = {}
    if next(plotSelection) then
        for _, row in ipairs(rows) do
            if plotSelection[row.folder] then chosen[#chosen + 1] = row end
        end
    else
        for index = 1, math.min(PLOT_SERIES, #rows) do chosen[index] = rows[index] end
    end

    -- One axis for every series, so heights stay comparable between lines.
    local series, yMax, yMin = {}, 0, math.huge
    for _, row in ipairs(chosen) do
        local values = ringValues(row.folder, {}, PLOT_POINTS)
        if #values > 1 then
            for _, v in ipairs(values) do
                if v > yMax then yMax = v end
                if v < yMin then yMin = v end
            end
            series[#series + 1] = { values = values, color = colorFor(row.folder) }
        end
    end
    if #series == 0 then
        for index = 1, #plot.lines do plot.lines[index]:Hide() end
        return
    end

    if fitToRange then
        -- The widening guard is RELATIVE to the magnitude on screen, not a
        -- fixed number of milliseconds. An absolute floor silently defeats the
        -- whole mode for a quiet UI: modules sitting around 0.02 ms have a real
        -- band far narrower than any fixed floor worth setting, so they all got
        -- widened to that floor and squashed into part of the panel. A band
        -- that is small but real is exactly what this mode exists to magnify;
        -- only a band that is negligible against its own magnitude is noise.
        local minRange = math.max(yMax * 0.02, 1e-5)
        if yMax - yMin < minRange then
            local mid = (yMax + yMin) / 2
            -- Anchor at zero if centring would go negative, but keep the range
            -- rather than replacing it: the old code reset yMax here too, which
            -- is what turned a fitted axis back into a zero-based one.
            yMin = math.max(0, mid - minRange / 2)
            yMax = yMin + minRange
        end
    else
        -- Zero-based, floored so an idle UI does not amplify rounding noise
        -- into a dramatic-looking trace.
        yMin = 0
        if yMax < PLOT_FLOOR then yMax = PLOT_FLOOR end
    end
    local range = math.max(yMax - yMin, 1e-6)

    -- Clamped at both ends: in min-max mode a value can sit below the floor
    -- after the range was widened, and a negative offset would draw off-panel.
    local function plotY(value)
        local fraction = (value - yMin) / range
        return math.max(0, math.min(fraction, 1)) * height
    end

    local used = 0
    for _, entry in ipairs(series) do
        local values, color = entry.values, entry.color
        local count = #values
        -- Anchor to the full PLOT_POINTS span so the trace grows in from the
        -- right as history fills, instead of stretching to fit and making a
        -- short history look like a long one.
        local step = width / (PLOT_POINTS - 1)
        local firstX = width - (count - 1) * step
        for i = 1, count - 1 do
            used = used + 1
            local line = plotLine(plot, used)
            line:SetColorTexture(color[1], color[2], color[3], 0.95)
            line:SetStartPoint("BOTTOMLEFT", plot,
                firstX + (i - 1) * step, plotY(values[i]))
            line:SetEndPoint("BOTTOMLEFT", plot,
                firstX + i * step, plotY(values[i + 1]))
            line:Show()
        end
    end
    for index = used + 1, #plot.lines do plot.lines[index]:Hide() end

    -- Always milliseconds, in both modes. The gridlines sit at the top and the
    -- middle of the panel, so the middle label is the midpoint of the axis
    -- range, which is only yMax/2 when the axis is zero-based.
    local span = floor(PLOT_POINTS * monitorInterval + 0.5)
    local label = axisLabeller(yMin, yMax)
    plot.topLabel:SetText(label(yMax))
    plot.midLabel:SetText(label((yMax + yMin) / 2))
    plot.bottomLabel:SetText(label(yMin))
    plot.spanLabel:SetText(format("%ds window, %s", span,
        fitToRange and "axis fit to range" or "zero-based axis"))
end

-------------------------------------------------------------------------------
-- Table
-------------------------------------------------------------------------------
-- Share of frame, colored by how much explaining it needs.
local function shareColor(pct)
    if pct >= 10 then return 1.0, 0.3, 0.3 end
    if pct >= 5 then return 1.0, 0.7, 0.2 end
    if pct >= 2 then return 1.0, 1.0, 1.0 end
    return 0.6, 0.7, 0.6
end

local function makeRow(parent, index, isHeader)
    local row = { cells = {} }
    for _, column in ipairs(COLUMNS) do
        local text = parent:CreateFontString(nil, "ARTWORK",
            isHeader and "GameFontNormalSmall" or "GameFontHighlightSmall")
        text:SetPoint("TOPLEFT", parent, "TOPLEFT", column.x, -(28 + index * ROW_HEIGHT))
        text:SetWidth(column.w)
        text:SetJustifyH(column.justify)
        row.cells[column.key] = text
    end
    return row
end

local function updateMonitor()
    if not monitor or not monitor:IsShown() then return end

    local appMs = appMetric(Metric.RecentAverageTime)
    local allAddonMs = overallMetric(Metric.RecentAverageTime)

    local refreshedMemory = false
    if memoryElapsed >= MEMORY_INTERVAL then
        memoryElapsed = 0
        refreshMemory()
        refreshedMemory = true
    end

    local mode = AVERAGE_MODES[averageIndex] or AVERAGE_MODES[1]
    local smoothing = (averageIndex > 1) and mode or nil

    local rows = {}
    for _, mod in ipairs(euiModules()) do
        local live = metric(mod.folder, Metric.RecentAverageTime)
        pushSample(mod.folder, live)
        rows[#rows + 1] = {
            display = mod.display,
            folder  = mod.folder,
            live    = live,
            shown   = smoothing and averageOf(mod.folder, smoothing) or live,
            peak    = metric(mod.folder, Metric.PeakTime),
            hitches = Metric.CountTimeOver10Ms and metric(mod.folder, Metric.CountTimeOver10Ms) or 0,
            kb      = refreshedMemory and memoryKB(mod.folder) or nil,
        }
    end
    -- Sort on what is displayed, or the rows reshuffle under the numbers.
    sort(rows, function(a, b) return a.shown > b.shown end)

    -- Which modules are actually on the graph right now, so the table can show
    -- it without re-deriving the rule in two places.
    local plotted, autoMode = {}, not next(plotSelection)
    if autoMode then
        for index = 1, math.min(PLOT_SERIES, #rows) do plotted[rows[index].folder] = true end
    else
        for folder in pairs(plotSelection) do plotted[folder] = true end
    end

    local totalShown = 0
    for index = 1, MONITOR_MAX_ROWS do
        local row, data = monitor.rows[index], rows[index]
        if not row then break end
        row.folder = data and data.folder or nil
        if row.button then row.button:SetShown(data ~= nil) end
        if not data then
            for _, cell in pairs(row.cells) do cell:SetText("") end
        else
            totalShown = totalShown + data.shown
            local pct = sharePercent(data.shown, appMs, allAddonMs)
            row.cells.display:SetText(data.display)
            row.cells.recent:SetText(msText(data.shown))
            row.cells.share:SetText(format("%.2f%%", pct))
            row.cells.peak:SetText(msText(data.peak))
            row.cells.hitches:SetText(data.hitches > 0 and tostring(data.hitches) or "-")
            -- Memory refreshes on its own slower cycle; leave the last reading
            -- up rather than blanking the column in between.
            if data.kb then row.cells.kb:SetText(formatKB(data.kb)) end
            row.cells.share:SetTextColor(shareColor(pct))
            row.cells.hitches:SetTextColor(data.hitches > 0 and 1 or 0.5,
                data.hitches > 0 and 0.7 or 0.5, data.hitches > 0 and 0.2 or 0.5)
            -- The table doubles as the graph's legend: a plotted row wears its
            -- own line color, and a pinned one is marked so an idle module you
            -- deliberately kept on the graph does not look like an accident.
            local onGraph = graphShown and plotted[data.folder]
            if onGraph then
                local color = colorFor(data.folder)
                row.cells.display:SetTextColor(color[1], color[2], color[3])
                row.cells.display:SetText((plotSelection[data.folder] and "* " or "") .. data.display)
            elseif graphShown then
                row.cells.display:SetTextColor(0.55, 0.55, 0.55)
            else
                row.cells.display:SetTextColor(1, 1, 1)
            end
        end
    end

    updateGraph(monitor.plot, rows)

    local fps = GetFramerate and GetFramerate() or 0
    local span = historyStarted and (GetTime() - historyStarted) or 0
    local statusLabel = mode.status
    if mode.session then statusLabel = format("%s (%ds):", statusLabel, floor(span)) end
    monitor.status:SetText(format(
        "%s  EUI %s ms (%.2f%%)  |  all addons %s ms  |  %.0f fps (%.1f ms budget)",
        statusLabel,
        msText(totalShown), sharePercent(totalShown, appMs, allAddonMs), msText(allAddonMs),
        fps, fps > 0 and (1000 / fps) or 0))
end

monitorDriver:SetScript("OnUpdate", function(self, delta)
    monitorElapsed = monitorElapsed + delta
    memoryElapsed = memoryElapsed + delta
    if monitorElapsed < monitorInterval then return end
    monitorElapsed = 0
    updateMonitor()
end)

-------------------------------------------------------------------------------
-- Window
-------------------------------------------------------------------------------
local function saveMonitorState()
    if not ns.db then return end
    ns.db.monitor = ns.db.monitor or {}
    return ns.db.monitor
end

local function applyGraphLayout()
    if not monitor then return end
    monitor:SetHeight(graphShown and (BASE_HEIGHT + GRAPH_HEIGHT) or BASE_HEIGHT)
    monitor.plot:SetShown(graphShown)
    monitor.graphButton:SetText(graphShown and "Graph: on" or "Graph: off")
    local state = saveMonitorState()
    if state then state.graph = graphShown end
end

local function applyAverageMode()
    if not monitor then return end
    local mode = AVERAGE_MODES[averageIndex] or AVERAGE_MODES[1]
    monitor.averageButton:SetText("Avg: " .. mode.label)
    monitor.header.cells.recent:SetText(mode.header)
    local state = saveMonitorState()
    if state then state.average = averageIndex end
end

local function applyScaleMode()
    if not monitor then return end
    monitor.scaleButton:SetText(fitToRange and "Scale: min-max" or "Scale: 0-max")
    local state = saveMonitorState()
    if state then state.normalize = fitToRange end
end

local function applySeriesLabel()
    if not monitor then return end
    local n = selectionCount()
    monitor.seriesLabel:SetText(n > 0 and format("Series: %d", n) or "Series: auto")
    local state = saveMonitorState()
    if state then
        -- Persist the pinned set, so the modules you are investigating survive
        -- the reloads that investigating them tends to involve.
        local saved = {}
        for folder in pairs(plotSelection) do saved[#saved + 1] = folder end
        sort(saved)
        state.series = saved
    end
end

-- Clicking a row pins or unpins that module. Turning the graph on at the same
-- time is the obviously-intended thing: picking a series is only ever a
-- prelude to looking at it.
local function toggleRow(index)
    if not monitor then return end
    local folder = monitor.rows[index] and monitor.rows[index].folder
    if not folder then return end
    plotSelection[folder] = (not plotSelection[folder]) or nil
    if next(plotSelection) and not graphShown then
        graphShown = true
        applyGraphLayout()
    end
    applySeriesLabel()
    updateMonitor()
end

-- Bulk forms of the row click. Pinning twenty modules one row at a time to
-- compare them is the case a per-row toggle is worst at, and unpinning them
-- afterwards is worse still.
--
-- None CLEARS the pins rather than emptying the graph. With nothing pinned the
-- plot falls back to the busiest few on its own, which is the useful reading of
-- an empty selection and the state the window opens in. A genuinely blank graph
-- is what the Graph button is for.
local function selectAllSeries()
    if not monitor then return end
    for _, mod in ipairs(euiModules()) do plotSelection[mod.folder] = true end
    -- Same courtesy the row click pays: choosing series is only ever a prelude
    -- to looking at them.
    if not graphShown then
        graphShown = true
        applyGraphLayout()
    end
    applySeriesLabel()
    updateMonitor()
end

local function selectNoSeries()
    if not monitor then return end
    wipe(plotSelection)
    applySeriesLabel()
    updateMonitor()
end

local function makeButton(parent, label, width, onClick)
    local button = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    button:SetSize(width, 20)
    button:SetText(label)
    button:SetScript("OnClick", onClick)
    return button
end

local function buildMonitor()
    if monitor then return monitor end
    local frame = CreateFrame("Frame", "EUIDiagCPUMonitor", UIParent,
        "BasicFrameTemplateWithInset")
    -- CreateFrame hands back a SHOWN frame. Without this the first
    -- `/euidiag cpu window` builds it visible, sees IsShown() and toggles it
    -- straight back off, so the window only appears on the second try.
    frame:Hide()
    frame:SetSize(MONITOR_WIDTH, BASE_HEIGHT)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
        -- Remember where it was put, so a profiling session that reloads a
        -- dozen times does not start with the window back in the middle.
        local state = saveMonitorState()
        if state then
            local point, _, relativePoint, x, y = self:GetPoint()
            state.point = { point, relativePoint, x, y }
        end
    end)
    frame:SetFrameStrata("HIGH")
    frame.TitleText:SetText("EllesmereUI CPU")

    frame.rows = {}
    frame.header = makeRow(frame, 0, true)
    for _, column in ipairs(COLUMNS) do
        frame.header.cells[column.key]:SetText(column.title)
    end
    for index = 1, MONITOR_MAX_ROWS do
        local row = makeRow(frame, index, false)
        -- A click target over the whole row. Dragging still moves the window:
        -- without forwarding, the rows would become a dead zone covering most
        -- of the frame.
        local button = CreateFrame("Button", nil, frame)
        button:SetPoint("TOPLEFT", frame, "TOPLEFT", 8, -(28 + index * ROW_HEIGHT))
        button:SetSize(MONITOR_WIDTH - 24, ROW_HEIGHT)
        button:RegisterForDrag("LeftButton")
        button:SetScript("OnDragStart", function() frame:StartMoving() end)
        button:SetScript("OnDragStop", function()
            frame:StopMovingOrSizing()
            local state = saveMonitorState()
            if state then
                local point, _, relativePoint, x, y = frame:GetPoint()
                state.point = { point, relativePoint, x, y }
            end
        end)
        button:SetScript("OnClick", function() toggleRow(index) end)

        local highlight = button:CreateTexture(nil, "HIGHLIGHT")
        highlight:SetAllPoints(button)
        highlight:SetColorTexture(1, 1, 1, 0.08)

        row.button = button
        frame.rows[index] = row
    end

    -- Graph panel. Anchored top and bottom, so growing the window by
    -- GRAPH_HEIGHT is all it takes to give it room.
    local plot = CreateFrame("Frame", nil, frame)
    plot:SetPoint("TOPLEFT", frame, "TOPLEFT", 12, -TABLE_BOTTOM)
    plot:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -12, CONTROLS_HEIGHT)
    plot:Hide()
    plot.lines = {}

    local background = plot:CreateTexture(nil, "BACKGROUND")
    background:SetAllPoints(plot)
    background:SetColorTexture(0, 0, 0, 0.35)

    for _, fraction in ipairs({ 1, 0.5 }) do
        local grid = plot:CreateTexture(nil, "BORDER")
        grid:SetColorTexture(1, 1, 1, 0.12)
        grid:SetHeight(1)
        grid:SetPoint("BOTTOMLEFT", plot, "BOTTOMLEFT", 0, fraction * GRAPH_HEIGHT - 1)
        grid:SetPoint("BOTTOMRIGHT", plot, "BOTTOMRIGHT", 0, fraction * GRAPH_HEIGHT - 1)
    end

    plot.topLabel = plot:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    plot.topLabel:SetPoint("TOPLEFT", plot, "TOPLEFT", 3, -2)
    plot.midLabel = plot:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    plot.midLabel:SetPoint("LEFT", plot, "LEFT", 3, 0)
    -- The axis floor is only zero in zero-based mode, so it has to be printed
    -- rather than assumed.
    plot.bottomLabel = plot:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    plot.bottomLabel:SetPoint("BOTTOMLEFT", plot, "BOTTOMLEFT", 3, 2)
    plot.spanLabel = plot:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    plot.spanLabel:SetPoint("BOTTOMRIGHT", plot, "BOTTOMRIGHT", -3, 2)
    frame.plot = plot

    frame.averageButton = makeButton(frame, "Average: off", 100, function()
        averageIndex = averageIndex % #AVERAGE_MODES + 1
        applyAverageMode()
        updateMonitor()
    end)
    frame.averageButton:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 12, 28)

    frame.graphButton = makeButton(frame, "Graph: off", 90, function()
        graphShown = not graphShown
        applyGraphLayout()
        updateMonitor()
    end)
    frame.graphButton:SetPoint("LEFT", frame.averageButton, "RIGHT", 6, 0)

    frame.scaleButton = makeButton(frame, "Scale: 0-max", 104, function()
        fitToRange = not fitToRange
        applyScaleMode()
        -- Turning the graph on with it: rescaling a plot nobody can see is
        -- not what anyone meant by pressing this.
        if not graphShown then
            graphShown = true
            applyGraphLayout()
        end
        updateMonitor()
    end)
    frame.scaleButton:SetPoint("LEFT", frame.graphButton, "RIGHT", 6, 0)

    -- A readout, not a control. It used to be a button whose only action was to
    -- clear the pins, which is now what None does and says.
    frame.seriesLabel = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
    frame.seriesLabel:SetSize(96, 20)
    frame.seriesLabel:SetJustifyH("CENTER")
    frame.seriesLabel:SetText("Series: auto")
    frame.seriesLabel:SetPoint("LEFT", frame.scaleButton, "RIGHT", 6, 0)

    frame.allButton = makeButton(frame, "All", 48, selectAllSeries)
    frame.allButton:SetPoint("LEFT", frame.seriesLabel, "RIGHT", 6, 0)

    frame.noneButton = makeButton(frame, "None", 56, selectNoSeries)
    frame.noneButton:SetPoint("LEFT", frame.allButton, "RIGHT", 6, 0)

    frame.resetButton = makeButton(frame, "Reset", 62, function()
        resetHistory()
        updateMonitor()
    end)
    frame.resetButton:SetPoint("LEFT", frame.noneButton, "RIGHT", 6, 0)

    local status = frame:CreateFontString(nil, "ARTWORK", "GameFontDisableSmall")
    status:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 12, 10)
    status:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -12, 10)
    status:SetJustifyH("LEFT")
    frame.status = status

    -- The driver only runs while the window is up, so a closed monitor costs
    -- nothing at all rather than a cheap tick.
    frame:SetScript("OnShow", function()
        monitorElapsed = monitorInterval
        memoryElapsed = MEMORY_INTERVAL
        if not historyStarted then resetHistory() end
        monitorDriver:Show()
        updateMonitor()
        local state = saveMonitorState()
        if state then state.open = true end
    end)
    frame:SetScript("OnHide", function()
        monitorDriver:Hide()
        local state = saveMonitorState()
        if state then state.open = false end
    end)

    -- Escape closes it, the way every other utility window behaves.
    if UISpecialFrames then
        UISpecialFrames[#UISpecialFrames + 1] = "EUIDiagCPUMonitor"
    end

    monitor = frame

    local saved = ns.db and ns.db.monitor
    if saved then
        -- Older saves stored a boolean here; true meant the cumulative mean.
        if type(saved.average) == "number" then
            averageIndex = math.min(math.max(saved.average, 1), #AVERAGE_MODES)
        elseif saved.average == true then
            averageIndex = #AVERAGE_MODES
        end
        graphShown = saved.graph or false
        fitToRange = saved.normalize or false
        if type(saved.series) == "table" then
            for _, folder in ipairs(saved.series) do plotSelection[folder] = true end
        end
        if saved.point then
            frame:ClearAllPoints()
            frame:SetPoint(saved.point[1], UIParent, saved.point[2], saved.point[3], saved.point[4])
        end
    end
    applyAverageMode()
    applyGraphLayout()
    applyScaleMode()
    applySeriesLabel()
    return frame
end

function ns.PerfWindow(args)
    if not profilerReady() then return end
    local sub = (args[2] or ""):lower()
    local frame = buildMonitor()

    if sub == "avg" or sub == "average" then
        averageIndex = averageIndex % #AVERAGE_MODES + 1
        applyAverageMode()
        if not frame:IsShown() then frame:Show() else updateMonitor() end
        outf("smoothing: %s", AVERAGE_MODES[averageIndex].label)
        return
    elseif sub == "graph" or sub == "plot" then
        graphShown = not graphShown
        applyGraphLayout()
        if not frame:IsShown() then frame:Show() else updateMonitor() end
        outf("graph %s", graphShown and "on" or "off")
        return
    elseif sub == "scale" or sub == "fit" or sub == "normalize" then
        fitToRange = not fitToRange
        applyScaleMode()
        if not graphShown then
            graphShown = true
            applyGraphLayout()
        end
        if not frame:IsShown() then frame:Show() else updateMonitor() end
        outf("graph axis: %s", fitToRange and "fit to range (min-max)" or "zero-based")
        return
    elseif sub == "reset" then
        resetHistory()
        updateMonitor()
        emit("monitor history reset")
        return
    end

    local wanted = tonumber(sub)
    if wanted then
        monitorInterval = math.max(0.1, wanted)
        -- The plot's span is PLOT_POINTS samples wide, so its window in
        -- seconds moves with the interval; say so rather than let the axis
        -- label quietly mean something new.
        outf("monitor updating every %gs (graph spans %ds)",
            monitorInterval, floor(PLOT_POINTS * monitorInterval + 0.5))
        if not frame:IsShown() then frame:Show() end
        return
    end
    if frame:IsShown() then
        frame:Hide()
        emit("monitor closed")
    else
        frame:Show()
        outf("monitor open, updating every %gs — click a row to pin it to the graph",
            monitorInterval)
    end
end

-- Reopen where it was left, so a reload mid-profiling does not cost the window.
ns.OnDB(function(db)
    if db.monitor and db.monitor.open and C_AddOnProfiler then
        buildMonitor():Show()
    end
end)

-------------------------------------------------------------------------------
-- Recorder
-------------------------------------------------------------------------------
-- Samples land in SavedVariables, which the client writes to
--   WTF/Account/<account>/SavedVariables/EllesmereUISecretsDiag.lua
-- on /reload or logout. That file is the log the offline analyzer in
-- .tools/perf/ reads to produce CSV and per-module statistics.
--
-- Each recording carries its own `columns` list and stores samples as flat
-- arrays in that order, so the analyzer needs no knowledge of module names or
-- of which metrics were captured — it just zips columns against values.
-- One second, because the thing this is for is a dungeon: a pull lasts under a
-- minute and a bad frame lasts a moment, and a five-second sample cannot see
-- either. At one second a 35-minute key is about 2100 samples.
local DEFAULT_INTERVAL = 1
local DEFAULT_CAP = 10000

-- Sub-second sampling is allowed, down to twenty samples a second. Below that
-- the timer is bound by the frame rate anyway -- OnUpdate runs once a frame, so
-- an interval shorter than a frame just samples every frame.
--
-- What it can and cannot sharpen is worth knowing before choosing one:
-- RecentAverageTime is the average over the last 60 frames, so two samples
-- taken a tenth of a second apart share most of their frames and the CPU
-- columns cannot resolve anything finer than that window. Frame rate, memory,
-- and WHEN a spike arrives do get sharper. RecStart says this at the time.
local MIN_INTERVAL = 0.05
local METRIC_FRAMES = 60

-- The cap is a count, but nobody chooses a recording in samples -- they choose
-- it in "one dungeon". So the default cap is derived from the interval to hold
-- roughly this many minutes, which is a key plus the walk in.
--
-- Never below DEFAULT_CAP, so the one-second default keeps the long headroom it
-- had. Never above what fits DEFAULT_HEAP_KB, because the cost that bites at a
-- tenth of a second is Lua heap, and a cap that quietly allows 160 MB of it is
-- not a default. An explicit second argument overrides both ends.
local DEFAULT_MINUTES = 45
local DEFAULT_HEAP_KB = 80 * 1024

-- Memory reads on their own slower cycle. UpdateAddOnMemoryUsage walks EVERY
-- installed addon, not only ours, which is affordable once every few seconds
-- and not affordable on every tick of a dungeon-rate recording. Memory also
-- moves far too slowly to be worth a reading a second. The last figures are
-- repeated into the samples in between, so the column layout is unchanged and
-- the analyzer needs to know nothing about this.
local MEMORY_EVERY = 5

-- Written to five decimals. The client serializes a full double otherwise --
-- seventeen characters for a reading whose useful precision is five -- and the
-- file is one number per line, so those digits are most of its size.
local function store(value)
    if type(value) ~= "number" then return 0 end
    return floor(value * 100000 + 0.5) / 100000
end

-- One value per line. Both figures are measured against a real recording, not
-- reasoned about: a 10000-sample run of 67 columns wrote a 5.08 MB file (7.95
-- bytes a value) while this addon's own memory column rose 29 MB (2.93 KB a
-- sample, about 45 bytes a value).
--
-- The heap figure is the larger of the two and the one nobody expects. Lua
-- grows a table's array part by doubling, so a 67-value sample sits in 128
-- slots and pays for 61 it never uses. It is also paid TWICE: once while
-- recording, and again at every login afterwards, when the client reads the
-- file back into the same shape. That is what makes an undropped recording
-- expensive rather than merely large.
local BYTES_PER_VALUE = 8

-- Heap is charged by slot, not by value: Lua grows a table's array part by
-- doubling, so a row of N values occupies the next power of two of slots. The
-- per-slot figure is calibrated against that same run -- 67 values took 128
-- slots and cost 2.93 KB a sample -- and so it includes the table header and
-- the garbage still waiting on the collector, which is what the player's
-- memory figure actually shows.
local HEAP_BYTES_PER_SLOT = 23

local function heapKBPerRow(values)
    local slots = 1
    while slots < values do slots = slots * 2 end
    return slots * HEAP_BYTES_PER_SLOT / 1024
end

local function recBytes(r)
    local values = #(r.samples or {}) * #(r.columns or {})
    -- The side tables are small by design, but "small" is a claim this should
    -- be able to back up rather than assume.
    values = values + #(r.memory or {}) * (#(r.modules or {}) + 1)
    values = values + #(r.peaks or {}) * 3
    return values * BYTES_PER_VALUE
end

local function totalBytes(db)
    local total = 0
    for _, r in ipairs(db.recordings or {}) do total = total + recBytes(r) end
    return total
end

-- Stored recordings are re-read by the client at EVERY login, not only by the
-- session that wants them, so a forgotten pile is a permanent load-time cost
-- rather than idle disk. Said wherever the user is already looking straight at
-- the thing they would have to drop.
local function dropReminder(db)
    db = db or ns.db
    if not db or #db.recordings == 0 then return end
    outf("  %d recording(s) held, about %s — re-read at every login",
        #db.recordings, formatKB(totalBytes(db) / 1024))
    emit("  /euidiag rec drop all  once you have exported what you need")
end

local rec = {
    active = false,
    interval = DEFAULT_INTERVAL,
    elapsed = 0,
    current = nil,   -- the recording table inside SavedVariables
}

-- Our own frame, created in this file's main chunk, so the sampler's cost is
-- billed to this addon and shows up in its own table rather than hiding inside
-- whichever Blizzard frame a C_Timer ticker happens to live on.
local sampler = CreateFrame("Frame")
sampler:Hide()

-- Only the readings that actually differ from one sample to the next. Memory
-- and PeakTime used to have a column each per module, and measured over a real
-- 10000-sample run they were 63% of every row and 1.2% of the information:
-- memory refreshes every few seconds however fast the sampler runs, and
-- PeakTime is a high-water mark that changed five times in 210,000 stored
-- values. Both now live in their own tables, on their own cadence, below.
local function moduleNames(modules)
    local names = {}
    for i, mod in ipairs(modules) do names[i] = mod.folder end
    return names
end

local function buildColumns(modules)
    local columns = { "t", "fps", "app_ms", "all_addons_ms" }
    for _, mod in ipairs(modules) do columns[#columns + 1] = mod.folder .. "_ms" end
    return columns
end

local function takeSample()
    local r = rec.current
    if not r then return end

    local modules = r._modules
    local now = floor((GetTime() - r._startTime) * 10 + 0.5) / 10

    -- Memory keeps its own table on the MEMORY_EVERY cycle. Reading it walks
    -- every installed addon, so the cadence cannot follow the sample rate, and
    -- once it does not, a column per sample only repeats the last figure.
    local everyN = math.max(1, floor(MEMORY_EVERY / r.interval + 0.5))
    if (#r.samples % everyN) == 0 then
        refreshMemory()
        local row = { now }
        for i, mod in ipairs(modules) do
            row[i + 1] = floor(memoryKB(mod.folder) + 0.5)
        end
        r.memory[#r.memory + 1] = row
    end

    -- A rise in PeakTime is an event: this module just had its worst frame of
    -- the session. Between rises the number is unchanged by definition, so only
    -- the rises are recorded, each as {t, module index, ms}. The first reading
    -- of each module lands at once and carries whatever it did while loading.
    for i, mod in ipairs(modules) do
        local peak = store(metric(mod.folder, Metric.PeakTime))
        if peak > (r._peak[i] or -1) then
            r._peak[i] = peak
            r.peaks[#r.peaks + 1] = { now, i, peak }
        end
    end

    local fps = GetFramerate and GetFramerate() or 0
    local sample = {
        now,
        floor(fps * 10 + 0.5) / 10,
        store(appMetric(Metric.RecentAverageTime)),
        store(overallMetric(Metric.RecentAverageTime)),
    }
    for _, mod in ipairs(modules) do
        sample[#sample + 1] = store(metric(mod.folder, Metric.RecentAverageTime))
    end
    r.samples[#r.samples + 1] = sample

    if #r.samples >= r.cap then
        ns.RecStop("sample cap reached")
    end
end

sampler:SetScript("OnUpdate", function(self, delta)
    rec.elapsed = rec.elapsed + delta
    if rec.elapsed < rec.interval then return end
    -- Carry the overshoot rather than zeroing it. A frame is 8-16ms, so zeroing
    -- throws away up to a frame per sample: at one second that is noise, at a
    -- tenth it is a tenth of the rate, and every timestamp late by the end.
    rec.elapsed = rec.elapsed - rec.interval
    -- Except after a hitch, or when the interval is shorter than a frame. There
    -- is one sample per frame at most, so a backlog can only ever be paid off
    -- by running fast later, which is worse than dropping it.
    if rec.elapsed > rec.interval then rec.elapsed = 0 end
    takeSample()
end)

-- Marks give the offline graphs their vertical lines: without them a spike in
-- the trace is unattributable, and "the pull started here" is the single most
-- useful thing to know about a CPU trace.
local MARK_EVENTS = {
    PLAYER_REGEN_DISABLED    = "combat-start",
    PLAYER_REGEN_ENABLED     = "combat-end",
    ENCOUNTER_START          = "encounter-start",
    ENCOUNTER_END            = "encounter-end",
    CHALLENGE_MODE_START     = "keystone-start",
    CHALLENGE_MODE_COMPLETED = "keystone-end",
    ZONE_CHANGED_NEW_AREA    = "zone-change",
}

local function onMarkEvent(self, event, ...)
    local r = rec.current
    if not r then return end
    local label = MARK_EVENTS[event] or event
    if event == "ENCOUNTER_START" or event == "ENCOUNTER_END" then
        local _, encounterName = ...
        if type(encounterName) == "string" then label = label .. ": " .. encounterName end
    end
    r.marks[#r.marks + 1] = {
        floor((GetTime() - r._startTime) * 10 + 0.5) / 10,
        label,
    }
end

local function setMarkEvents(on)
    for event in pairs(MARK_EVENTS) do
        if on then
            ns.addon:RegisterEvent(event, onMarkEvent)
        else
            ns.addon:UnregisterEvent(event)
        end
    end
end

function ns.RecStart(args)
    if not profilerReady() then return end
    if rec.active then
        result("WARN", "rec start", "already recording — /euidiag rec stop first")
        return
    end
    local db = ns.db
    if not db then result("ERR", "rec start", "SavedVariables not loaded yet"); return end

    local interval = tonumber(args[1]) or DEFAULT_INTERVAL
    if interval < MIN_INTERVAL then interval = MIN_INTERVAL end

    local modules = euiModules()
    if #modules == 0 then
        result("FAIL", "rec start", "no EllesmereUI modules are loaded")
        return
    end

    -- Column count is known only now, and it sets what a sample costs.
    local columns = buildColumns(modules)
    local heapPerSample = heapKBPerRow(#columns)
    local cap = tonumber(args[2])
    if not cap then
        cap = math.ceil(DEFAULT_MINUTES * 60 / interval)
        if cap < DEFAULT_CAP then cap = DEFAULT_CAP end
        local room = floor(DEFAULT_HEAP_KB / heapPerSample)
        if cap > room then cap = room end
    end

    -- `UnitFullName and UnitFullName("player")` would truncate to one value and
    -- silently lose the realm, so call it on its own line.
    local name, realm
    if UnitFullName then name, realm = UnitFullName("player") end
    if not realm and GetRealmName then realm = GetRealmName() end
    local version, build = GetBuildInfo()

    local recording = {
        label      = date("%Y-%m-%d %H:%M:%S"),
        epoch      = GetServerTime and GetServerTime() or 0,
        interval   = interval,
        cap        = cap,
        character  = format("%s-%s", tostring(name or "?"), tostring(realm or "?")),
        build      = format("%s (%s)", tostring(version), tostring(build)),
        metric     = "RecentAverageTime",
        columns    = columns,
        samples    = {},
        marks      = {},
        -- The order the memory rows and the peak indices are written in. The
        -- sample columns name their module; these two tables index into this.
        modules    = moduleNames(modules),
        memory     = {},
        peaks      = {},
        -- Leading underscore: runtime-only, stripped before this reaches disk.
        _modules   = modules,
        _peak      = {},
        _startTime = GetTime(),
    }
    db.recordings[#db.recordings + 1] = recording

    rec.active = true
    rec.current = recording
    rec.interval = interval
    rec.elapsed = 0
    setMarkEvents(true)
    sampler:Show()

    -- What the cap actually means in minutes and megabytes, said once at the
    -- start. A sample count answers neither question anyone has.
    outf("recording #%d started — %d modules, one sample every %gs",
        #db.recordings, #modules, interval)
    outf("  cap %d samples = %.1f min, about %s on disk and %s of Lua heap",
        cap, cap * interval / 60,
        formatKB(cap * #columns * BYTES_PER_VALUE / 1024),
        formatKB(cap * heapPerSample))

    -- Said only when it applies, and said with the player's own frame rate in
    -- it, because "60 frames" means a different number of seconds to everyone.
    local window = METRIC_FRAMES / math.max(1, GetFramerate and GetFramerate() or 60)
    if interval < window then
        outf("  note: the CPU columns average the last %d frames, about %.2fs at your",
            METRIC_FRAMES, window)
        emit("  frame rate, so samples this close together re-read the same frames.")
        emit("  Frame rate, memory and the timing of a spike do still get sharper.")
    end
    emit("  /euidiag rec stop when you are done; the file is written on /reload or logout")
    takeSample()
end

function ns.RecStop(reason)
    if not rec.active then
        result("WARN", "rec stop", "not recording")
        return
    end
    local r = rec.current
    sampler:Hide()
    setMarkEvents(false)
    rec.active = false
    rec.current = nil

    if r then
        r.duration = floor((GetTime() - r._startTime) * 10 + 0.5) / 10
        -- Drop the runtime-only fields: SavedVariables serialisation would
        -- otherwise try to write frame references and module tables to disk.
        r._modules = nil
        r._startTime = nil
        r._peak = nil
        outf("recording stopped%s — %d samples over %.1fs, %d mark(s), about %s",
            reason and (" (" .. reason .. ")") or "", #r.samples, r.duration or 0,
            #r.marks, formatKB(recBytes(r) / 1024))
        emit("  /euidiag rec export to read it here, or run .tools/perf/euidiag-perf.py")
        emit("  against the SavedVariables file for CSV and statistics")
        dropReminder()
    end
end

local function recStatus()
    local db = ns.db
    if not rec.active then
        emit("not recording")
        if db then outf("  %d stored recording(s) — /euidiag rec list", #db.recordings) end
        return
    end
    local r = rec.current
    if not r then
        result("ERR", "rec status", "marked active with no recording attached — /euidiag rec stop")
        return
    end
    outf("recording: %d samples, %.1fs elapsed, one every %gs (cap %d)",
        #r.samples, GetTime() - r._startTime, rec.interval, r.cap)
    outf("  %d module(s), %d mark(s), about %s so far",
        #r._modules, #r.marks, formatKB(recBytes(r) / 1024))
end

local function recList()
    local db = ns.db
    if not db then result("ERR", "rec list", "SavedVariables not loaded yet"); return end
    if #db.recordings == 0 then emit("no stored recordings"); return end
    header("Stored recordings")
    for i, r in ipairs(db.recordings) do
        outf("  #%d  %s  %d samples @ %gs  %s  %s%s",
            i, r.label or "?", #(r.samples or {}), r.interval or 0,
            ns.Pad(formatKB(recBytes(r) / 1024), 9),
            r.character or "?", (r == rec.current) and "  (RECORDING)" or "")
    end
    emit("  /euidiag rec export [n] | /euidiag rec drop <n|all>")
    dropReminder(db)
end

local function recDrop(args)
    local db = ns.db
    if not db then result("ERR", "rec drop", "SavedVariables not loaded yet"); return end
    if (args[1] or ""):lower() == "all" then
        if rec.active then ns.RecStop("recordings dropped") end
        local n = #db.recordings
        wipe(db.recordings)
        outf("dropped %d recording(s)", n)
        return
    end
    local index = tonumber(args[1])
    if not index or not db.recordings[index] then
        result("ERR", "rec drop", "usage: /euidiag rec drop <n|all>")
        return
    end
    if db.recordings[index] == rec.current then ns.RecStop("recording dropped") end
    table.remove(db.recordings, index)
    outf("dropped recording #%d", index)
end

-- CSV here is for a quick look and for pasting into a spreadsheet. Anything
-- longer than a few hundred rows belongs in the offline analyzer: the edit box
-- gets unusable well before the recording does.
local EXPORT_ROW_LIMIT = 2000

local function recExport(args)
    local db = ns.db
    if not db then result("ERR", "rec export", "SavedVariables not loaded yet"); return end
    local index = tonumber(args[1]) or #db.recordings
    local r = db.recordings[index]
    if not r then result("ERR", "rec export", "no recording #" .. tostring(index)); return end

    local lines = { concat(r.columns, ",") }
    local rows = #r.samples
    local shown = math.min(rows, EXPORT_ROW_LIMIT)
    for i = 1, shown do
        local sample, cells = r.samples[i], {}
        for j = 1, #r.columns do
            local v = sample[j]
            cells[j] = (type(v) == "number") and format("%.4f", v) or tostring(v or "")
        end
        lines[#lines + 1] = concat(cells, ",")
    end
    if shown < rows then
        lines[#lines + 1] = format("# truncated: %d of %d rows — use .tools/perf/euidiag-perf.py", shown, rows)
    end
    if #r.marks > 0 then
        lines[#lines + 1] = "# marks: t,label"
        for _, mark in ipairs(r.marks) do
            lines[#lines + 1] = format("# %s,%s", tostring(mark[1]), tostring(mark[2]))
        end
    end
    ns.ShowExport(concat(lines, "\n"), format("Recording #%d — %s", index, r.label or "?"))
    dropReminder(db)
end

local function cmdRec(args)
    local sub = (args[1] or "status"):lower()
    local rest = {}
    for i = 2, #args do rest[i - 1] = args[i] end

    if sub == "start" then ns.RecStart(rest)
    elseif sub == "stop" then ns.RecStop()
    elseif sub == "status" then recStatus()
    elseif sub == "list" then recList()
    elseif sub == "drop" then recDrop(rest)
    elseif sub == "export" then recExport(rest)
    else
        emit("usage: /euidiag rec start [interval] [cap] | stop | status | list | export [n] | drop <n|all>")
        emit("  interval is in seconds, default 1, floor 0.05 — `rec start 0.1` samples")
        emit("  ten times a second. Finer costs file size at about the same ratio.")
        emit("  Drop what you are done with.")
        dropReminder()
    end
end

-- A recording left running across a reload would keep a dangling reference to a
-- table the client is about to rewrite, so close it out cleanly first.
ns.addon:RegisterEvent("PLAYER_LOGOUT", function()
    if rec.active then ns.RecStop("logout") end
end)

-- Unprompted, because the whole failure mode is that nobody thinks to look. A
-- pile this size is costing load time in every session until it is dropped.
local LOGIN_WARN_BYTES = 2 * 1024 * 1024
ns.OnDB(function(db)
    local bytes = totalBytes(db)
    if bytes < LOGIN_WARN_BYTES then return end
    result("WARN", "rec", format("%d stored recording(s), about %s",
        #db.recordings, formatKB(bytes / 1024)))
    emit("  this is re-read at every login — /euidiag rec list, /euidiag rec drop all")
end)

-------------------------------------------------------------------------------
ns.Command("cpu", {
    group = "Performance",
    usage = "cpu [window|all|spikes]",
    help  = "CPU per module; `window` is live with average+graph, `spikes` counts hitches",
    fn    = cmdCPU,
})

ns.Command("mem", {
    group = "Performance",
    usage = "mem",
    help  = "memory per module, with the delta since the last reading",
    fn    = cmdMem,
})

ns.Command("rec", {
    group = "Performance",
    usage = "rec start [interval] [cap]|stop|list|export",
    help  = "sample CPU and memory on a timer into SavedVariables (also: status, drop)",
    fn    = cmdRec,
})
