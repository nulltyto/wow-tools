-------------------------------------------------------------------------------
--  Secrets.lua  —  what is readable, and what is secret, right here right now
--
--    /euidiag secrets            run every probe that fits the current context
--    /euidiag secrets list       what the probes are
--    /euidiag secrets <key>      run one
--    /euidiag eval <lua>         classify whatever an expression returns
--
--  These are reference probes, not regression tests: they answer "is this
--  value secret in this context" for a data source, and the answer legitimately
--  changes with where you are standing. Solo in a city almost everything reads
--  plain; the same probe in an M+ pull is the interesting run. Where a probe
--  needs a particular context to say anything, it says so instead of failing.
--
--  Every API touch is pcall-wrapped. The live build drifts from the doc export
--  these were written against, and finding out where is half the point.
--  No probe writes to any EllesmereUI database.
-------------------------------------------------------------------------------
local ADDON_NAME, ns = ...

local format, floor = string.format, math.floor
local emit, outf, header, result = ns.emit, ns.outf, ns.header, ns.result
local classify, isSecret, try = ns.classify, ns.isSecret, ns.try
local dumpFields = ns.dumpFields
local Diag = ns.addon

-------------------------------------------------------------------------------
-- Probe registry
-------------------------------------------------------------------------------
-- Shared with Investigations.lua, which registers its parked probes as hidden:
-- still runnable by key, never part of a plain `/euidiag secrets` run.
local probes = {}
local probeOrder = {}

--- Register a probe.
--  opts.manual keeps it out of a run-everything pass (anything that arms a
--  probe, spawns UI or changes game state must be asked for by name).
--  opts.hidden additionally keeps it out of the default listing.
function ns.Probe(key, title, fn, opts)
    opts = opts or {}
    if type(opts) == "boolean" then opts = { manual = opts } end
    probes[key] = { key = key, title = title, fn = fn, manual = opts.manual, hidden = opts.hidden }
    probeOrder[#probeOrder + 1] = key
end

function ns.RunProbe(key)
    local probe = probes[key]
    if not probe then return false end
    header(probe.title)
    local ok, err = pcall(probe.fn)
    if not ok then result("ERR", probe.title, tostring(err)) end
    return true
end

-- Local alias so the probe bodies below read the way they always have.
local function addTest(key, title, fn, manualOnly)
    ns.Probe(key, title, fn, { manual = manualOnly })
end

-------------------------------------------------------------------------------
-- Shared helpers
-------------------------------------------------------------------------------
local EXTERNAL_SPELLS = {
    { 10060,  "Power Infusion" },
    { 33206,  "Pain Suppression" },
    { 102342, "Ironbark" },
    { 29166,  "Innervate" },
    { 51052,  "Anti-Magic Zone" },
    { 62618,  "Power Word: Barrier" },
    { 98008,  "Spirit Link Totem" },
    { 97462,  "Rallying Cry" },
    { 31821,  "Aura Mastery" },
    { 196718, "Darkness" },
    { 2825,   "Bloodlust" },
    { 32182,  "Heroism" },
    { 6940,   "Blessing of Sacrifice" },
    { 116849, "Life Cocoon" },
}

-- Player spellIDs worth testing, pulled live from the CooldownViewer config
-- (never secret per the doc export). Falls back to the GCD dummy spell.
local function collectCDMSpells(maxCount)
    local spells = {}
    if C_CooldownViewer and C_CooldownViewer.GetCooldownViewerCategorySet then
        for category = 0, 3 do
            local ok, ids = try(C_CooldownViewer.GetCooldownViewerCategorySet, category, false)
            if ok and type(ids) == "table" then
                for _, cooldownID in ipairs(ids) do
                    local ok2, info = try(C_CooldownViewer.GetCooldownViewerCooldownInfo, cooldownID)
                    if ok2 and type(info) == "table" and type(info.spellID) == "number" then
                        spells[#spells + 1] = info.spellID
                        if maxCount and #spells >= maxCount then return spells end
                    end
                end
            end
        end
    end
    if #spells == 0 then spells[1] = 61304 end -- "Global Cooldown"
    return spells
end

local function groupUnits(cap)
    local units = {}
    if IsInRaid() then
        for i = 1, math.min(GetNumGroupMembers(), cap or 10) do
            units[#units + 1] = "raid" .. i
        end
    elseif IsInGroup() then
        for i = 1, math.min(GetNumGroupMembers() - 1, 4) do
            units[#units + 1] = "party" .. i
        end
    end
    return units
end

local SECRECY_NAME = { [0] = "never", [1] = "ALWAYS", [2] = "contextual" }

local function secrecyLabel(v)
    if v == nil then return "?" end
    if isSecret(v) then return "SECRET" end
    return SECRECY_NAME[v] or tostring(v)
end

-- Hidden widgets for sink tests, created lazily.
local sinks
local function getSinks()
    if sinks then return sinks end
    local holder = CreateFrame("Frame", "EUISecretsDiagSinkHolder", UIParent)
    holder:SetSize(1, 1)
    holder:SetPoint("TOP", UIParent, "TOP", 0, 200) -- offscreen-ish; hidden anyway
    holder:Hide()
    sinks = {
        holder = holder,
        cd  = CreateFrame("Cooldown", "EUISecretsDiagCooldown", holder, "CooldownFrameTemplate"),
        bar = CreateFrame("StatusBar", "EUISecretsDiagBar", holder),
        fs  = holder:CreateFontString(nil, "ARTWORK", "GameFontNormal"),
        tex = holder:CreateTexture(nil, "ARTWORK"),
    }
    sinks.bar:SetMinMaxValues(0, 1)
    return sinks
end

local function resetSink(widget)
    if widget and widget.SetToDefaults then pcall(widget.SetToDefaults, widget) end
    -- SetToDefaults strips a FontString's font; restore it or later SetText
    -- probes fail with "Font not set"
    if widget and widget.SetFontObject and widget.GetObjectType
        and widget:GetObjectType() == "FontString" then
        pcall(widget.SetFontObject, widget, GameFontNormal)
    end
end

-------------------------------------------------------------------------------
-- Unit collection helpers, shared with Investigations.lua.
-------------------------------------------------------------------------------
local function existingUnits(prefix, cap)
    local t = {}
    for i = 1, (cap or 10) do
        local u = prefix .. i
        if UnitExists(u) then t[#t + 1] = u end
    end
    return t
end

-- Mobs worth probing here (target + nameplates + bosses), de-duplicated.
local function mobUnits()
    local t = {}
    if UnitExists("target") and UnitCanAttack("player", "target") then t[#t + 1] = "target" end
    for _, u in ipairs(existingUnits("nameplate", 8)) do t[#t + 1] = u end
    for _, u in ipairs(existingUnits("boss", 5)) do t[#t + 1] = u end
    return t
end

-- Find a secret spellId on any nearby unit's auras WITHOUT reading it (holds
-- the secret value opaquely). Returns secretValue, sourceLabel or nil.
local function findSecretAuraSpellId()
    if not (C_UnitAuras and C_UnitAuras.GetAuraDataByIndex) then return nil end
    local units = { "target", "player" }
    for _, u in ipairs(groupUnits(4)) do units[#units + 1] = u end
    for _, u in ipairs(existingUnits("nameplate", 8)) do units[#units + 1] = u end
    for _, unit in ipairs(units) do
        if UnitExists(unit) then
            for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
                for i = 1, 40 do
                    local ok, aura = try(C_UnitAuras.GetAuraDataByIndex, unit, i, filter)
                    if not ok or not aura then break end
                    local okF, v = pcall(function() return aura.spellId end)
                    if okF and isSecret(v) then
                        return v, format("%s %s#%d", unit, filter, i)
                    end
                end
            end
        end
    end
    return nil
end

-- Classify a readback + test whether it is branchable-plain (a plain value we
-- can compare/branch on) vs secret-contaminated (comparison would error).
local function branchProbe(label, value)
    if isSecret(value) then
        local ok = pcall(function() return value == value end)
        outf("    %-26s = SECRET (==self %s)", label, ok and "ok?!" or "errors — unbranchable")
        return "secret"
    end
    outf("    %-26s = %s (plain / branchable)", label, classify(value))
    return "plain"
end

-- Exported so the parked investigations can reuse them without a second copy.
ns.branchProbe = branchProbe
ns.EXTERNAL_SPELLS = EXTERNAL_SPELLS
ns.collectCDMSpells = collectCDMSpells
ns.groupUnits = groupUnits
ns.secrecyLabel = secrecyLabel
ns.getSinks = getSinks
ns.resetSink = resetSink
ns.existingUnits = existingUnits
ns.mobUnits = mobUnits
ns.findSecretAuraSpellId = findSecretAuraSpellId

-------------------------------------------------------------------------------
-- Probes
-------------------------------------------------------------------------------
addTest("ctx", "Context & restriction states", function()
    local version, build = GetBuildInfo()
    outf("client %s (build %s); issecretvalue=%s issecrettable=%s",
        tostring(version), tostring(build),
        tostring(issecretvalue ~= nil), tostring(issecrettable ~= nil))

    local inInstance, instanceType = IsInInstance()
    outf("IsInInstance=%s type=%s; InCombatLockdown=%s; group=%s",
        tostring(inInstance), tostring(instanceType), tostring(InCombatLockdown()),
        IsInRaid() and "raid" or IsInGroup() and "party" or "solo")

    if C_Secrets and C_Secrets.HasSecretRestrictions then
        local ok, r = try(C_Secrets.HasSecretRestrictions)
        result(ok and "INFO" or "ERR", "C_Secrets.HasSecretRestrictions()", classify(r))
    else
        result("SKIP", "C_Secrets.HasSecretRestrictions", "namespace missing")
    end

    if C_RestrictedActions and Enum.AddOnRestrictionType then
        for name, value in pairs(Enum.AddOnRestrictionType) do
            local ok, r = try(C_RestrictedActions.IsAddOnRestrictionActive, value)
            outf("  restriction %-14s = %s", tostring(name), ok and classify(r) or ("ERR " .. tostring(r)))
        end
    else
        result("SKIP", "C_RestrictedActions / Enum.AddOnRestrictionType", "missing")
    end

    local ok, r = try(C_ChatInfo and C_ChatInfo.InChatMessagingLockdown)
    result(ok and "INFO" or "SKIP", "InChatMessagingLockdown()", classify(r))
    ok, r = try(C_CombatLog and C_CombatLog.IsCombatLogRestricted)
    result(ok and "INFO" or "SKIP", "IsCombatLogRestricted()", classify(r))
end)

-------------------------------------------------------------------------------
addTest("auras", "Aura instance-ID enumeration & filter classification", function()
    if not (C_UnitAuras and C_UnitAuras.GetUnitAuraInstanceIDs) then
        result("FAIL", "C_UnitAuras.GetUnitAuraInstanceIDs", "API missing in this build")
        return
    end

    local units = { "player" }
    for _, u in ipairs(groupUnits(2)) do units[#units + 1] = u end
    if UnitExists("target") then units[#units + 1] = "target" end

    for _, unit in ipairs(units) do
        local filters = (unit == "target" and UnitCanAttack("player", "target"))
            and { "HARMFUL", "HARMFUL|PLAYER", "HARMFUL|RAID_PLAYER_DISPELLABLE", "HARMFUL|CROWD_CONTROL" }
            or  { "HELPFUL", "HELPFUL|PLAYER", "HELPFUL|EXTERNAL_DEFENSIVE", "HELPFUL|BIG_DEFENSIVE" }

        local baseFilter = filters[1]
        local sortRule = Enum.UnitAuraSortRule and Enum.UnitAuraSortRule.Expiration or nil
        local ok, ids = try(C_UnitAuras.GetUnitAuraInstanceIDs, unit, baseFilter, 40, sortRule)
        if not ok then
            -- signature may differ from the doc export; retry minimal form
            ok, ids = try(C_UnitAuras.GetUnitAuraInstanceIDs, unit, baseFilter)
        end
        if not ok then
            result("ERR", unit .. " GetUnitAuraInstanceIDs", tostring(ids))
        elseif type(ids) ~= "table" then
            result("FAIL", unit .. " GetUnitAuraInstanceIDs", "returned " .. classify(ids))
        else
            local n = #ids
            local firstClass = n > 0 and classify(ids[1]) or "n/a"
            result(n > 0 and (isSecret(ids[1]) and "SECRET" or "PASS") or "INFO",
                format("%s ids(%s)", unit, baseFilter),
                format("count=%d first=%s", n, firstClass))

            if n > 0 and not isSecret(ids[1]) and C_UnitAuras.IsAuraFilteredOutByInstanceID then
                for f = 2, #filters do
                    local filt = filters[f]
                    local passCount, secretCount, errCount = 0, 0, 0
                    for i = 1, math.min(n, 40) do
                        local ok2, filteredOut = try(C_UnitAuras.IsAuraFilteredOutByInstanceID, unit, ids[i], filt)
                        if not ok2 then errCount = errCount + 1
                        elseif isSecret(filteredOut) then secretCount = secretCount + 1
                        elseif filteredOut == false then passCount = passCount + 1 end
                    end
                    local tag = errCount > 0 and "ERR" or secretCount > 0 and "SECRET" or "PASS"
                    -- escape the flag separator: raw "|R..." is eaten as a UI escape code
                    result(tag, format("%s matches %s", unit, (filt:gsub("|", "||"))),
                        format("%d/%d match (secret=%d err=%d)", passCount, math.min(n, 40), secretCount, errCount))
                end
            end
        end
    end
    emit("re-run inside an M+ / raid in combat for the interesting case")
end)

-------------------------------------------------------------------------------
addTest("dur", "Duration objects & widget sinks", function()
    local s = getSinks()

    local function durationBattery(label, d)
        if type(d) ~= "table" and type(d) ~= "userdata" then
            result("FAIL", label, "no duration object: " .. classify(d))
            return
        end
        local ok, hs = try(d.HasSecretValues, d)
        local okZ, zero = try(d.IsZero, d)
        outf("  %s: HasSecretValues=%s IsZero=%s", label,
            ok and classify(hs) or "ERR", okZ and classify(zero) or "ERR")
        local _, rem = try(d.GetRemainingDuration, d)
        local _, exp = try(d.HasExpired, d)
        local _, act = try(d.IsActive, d)
        outf("    GetRemainingDuration=%s HasExpired=%s IsActive=%s",
            classify(rem), classify(exp), classify(act))

        resetSink(s.cd)
        local ok2, err2 = try(s.cd.SetCooldownFromDurationObject, s.cd, d)
        result(ok2 and "PASS" or "ERR", label .. " -> Cooldown:SetCooldownFromDurationObject", ok2 and nil or tostring(err2))
        if s.cd.HasAnySecretAspect then
            local ok3, asp = try(s.cd.HasAnySecretAspect, s.cd)
            outf("    cooldown HasAnySecretAspect=%s GetCooldownTimes=%s",
                ok3 and classify(asp) or "ERR", classify(select(2, try(s.cd.GetCooldownTimes, s.cd))))
        end

        resetSink(s.bar)
        if s.bar.SetTimerDuration then
            local ok4, err4 = try(s.bar.SetTimerDuration, s.bar, d)
            result(ok4 and "PASS" or "ERR", label .. " -> StatusBar:SetTimerDuration", ok4 and nil or tostring(err4))
        else
            result("SKIP", label .. " -> StatusBar:SetTimerDuration", "method missing")
        end
    end

    -- 1) aura-sourced duration (player's first buff)
    if C_UnitAuras and C_UnitAuras.GetAuraDuration and C_UnitAuras.GetUnitAuraInstanceIDs then
        local ok, ids = try(C_UnitAuras.GetUnitAuraInstanceIDs, "player", "HELPFUL", 5)
        if ok and type(ids) == "table" and #ids > 0 and not isSecret(ids[1]) then
            local ok2, d = try(C_UnitAuras.GetAuraDuration, "player", ids[1])
            if ok2 then durationBattery("aura-duration", d)
            else result("ERR", "GetAuraDuration", tostring(d)) end
        else
            result("SKIP", "aura-duration", "no readable player buff instance id")
        end
    end

    -- 2) spell-cooldown-sourced duration
    if C_Spell and C_Spell.GetSpellCooldownDuration then
        local spellID = collectCDMSpells(1)[1]
        local ok, d = try(C_Spell.GetSpellCooldownDuration, spellID)
        if ok then durationBattery(format("spell-cd-duration(%d)", spellID), d)
        else result("ERR", "GetSpellCooldownDuration", tostring(d)) end
    else
        result("SKIP", "C_Spell.GetSpellCooldownDuration", "API missing")
    end

    -- 3) enemy cast duration (needs a casting target)
    if UnitExists("target") and UnitCastingDuration then
        local ok, d = try(UnitCastingDuration, "target")
        if ok and d then durationBattery("target-cast-duration", d)
        else result("INFO", "target-cast-duration", "target not casting or API differs") end
    end

    -- 4) DurationTextBinding
    if C_DurationUtil and C_DurationUtil.CreateDurationTextBinding then
        local ok, binding = try(C_DurationUtil.CreateDurationTextBinding)
        result(ok and "PASS" or "ERR", "CreateDurationTextBinding()", ok and "created" or tostring(binding))
        if ok and binding and binding.SetFontString then
            local ok2, err2 = try(binding.SetFontString, binding, s.fs)
            result(ok2 and "PASS" or "ERR", "binding:SetFontString", ok2 and nil or tostring(err2))
        end
    else
        result("SKIP", "C_DurationUtil.CreateDurationTextBinding", "API missing")
    end
end)

-------------------------------------------------------------------------------
addTest("meter", "C_DamageMeter readability", function()
    if not C_DamageMeter then
        result("FAIL", "C_DamageMeter", "namespace missing")
        return
    end
    local ok, avail, reason = try(C_DamageMeter.IsDamageMeterAvailable)
    outf("IsDamageMeterAvailable=%s reason=%s", classify(avail), classify(reason))

    local ok2, sessions = try(C_DamageMeter.GetAvailableCombatSessions)
    if not ok2 then
        result("ERR", "GetAvailableCombatSessions", tostring(sessions))
    elseif type(sessions) == "table" then
        outf("sessions available: %d", #sessions)
        if sessions[1] then dumpFields("session[1]", sessions[1]) end
    end

    -- enum discovery (names differ between doc export and live sometimes)
    for _, enumName in ipairs({ "DamageMeterType", "DamageMeterSessionType", "DamageMeterCombatSessionType", "DamageMeterCombineSessionType" }) do
        local e = Enum[enumName]
        if e then
            local keys = {}
            for k in pairs(e) do keys[#keys + 1] = k end
            table.sort(keys)
            outf("Enum.%s = { %s }", enumName, table.concat(keys, " "))
        else
            outf("Enum.%s missing", enumName)
        end
    end

    local sessionEnum = Enum.DamageMeterSessionType or Enum.DamageMeterCombatSessionType
    local typeEnum = Enum.DamageMeterType
    if sessionEnum and typeEnum and C_DamageMeter.GetCombatSessionFromType then
        local current = sessionEnum.Current or sessionEnum.Overall
        local dmg = typeEnum.DamageDone
        local ok3, session = try(C_DamageMeter.GetCombatSessionFromType, current, dmg)
        if not ok3 then
            result("ERR", "GetCombatSessionFromType", tostring(session))
        elseif session == nil then
            result("INFO", "GetCombatSessionFromType", "nil (no session yet — fight something first)")
        else
            dumpFields("currentSession", session)
            -- find an array-ish member and dump its first entry too
            local okIter = pcall(function()
                for k, v in pairs(session) do
                    if type(v) == "table" and v[1] ~= nil then
                        dumpFields(format("currentSession.%s[1]", tostring(k)), v[1], "    ")
                        break
                    end
                end
            end)
            if not okIter then emit("    (nested iteration failed — possibly secret contents)")
        end
        end

        -- EXPIRED session drill: names stayed plain in combat on the first live
        -- run — if amounts are plain too, per-pull history works mid-dungeon
        if ok2 and type(sessions) == "table" and sessions[1]
            and C_DamageMeter.GetCombatSessionFromID then
            local sid = sessions[1].sessionID
            if type(sid) == "number" then
                local ok4, expired = try(C_DamageMeter.GetCombatSessionFromID, sid, dmg)
                if not ok4 then
                    result("ERR", "GetCombatSessionFromID(expired)", tostring(expired))
                elseif expired == nil then
                    result("INFO", "GetCombatSessionFromID(expired)", "nil")
                else
                    dumpFields(format("expiredSession[id=%d]", sid), expired)
                    pcall(function()
                        for k, v in pairs(expired) do
                            if type(v) == "table" and v[1] ~= nil then
                                dumpFields(format("expiredSession.%s[1]", tostring(k)), v[1], "    ")
                                break
                            end
                        end
                    end)
                end
            end
        end
    end
end)

-------------------------------------------------------------------------------
addTest("timeline", "C_EncounterTimeline visibility", function()
    if not C_EncounterTimeline then
        result("FAIL", "C_EncounterTimeline", "namespace missing")
        return
    end
    -- unfiltered first: GetEventList has no filters; then the sorted list with
    -- excludeTerminalStates=false, excludeHiddenEvents=false to catch events
    -- the default filters hide (e.g. script events on long/hidden tracks)
    local list
    local okRaw, raw = try(C_EncounterTimeline.GetEventList)
    if okRaw and type(raw) == "table" then
        outf("GetEventList (unfiltered): %d event(s)", #raw)
        list = raw
    end
    local okS, sorted = try(C_EncounterTimeline.GetSortedEventList, nil, nil, false, false)
    if okS and type(sorted) == "table" then
        outf("GetSortedEventList(nil,nil,false,false): %d event(s)", #sorted)
        if not list or #sorted > #list then list = sorted end
    elseif not okS then
        outf("GetSortedEventList ERR: %s", tostring(sorted))
    end
    if type(list) ~= "table" or #list == 0 then
        result("INFO", "timeline", "no active events — run this during a boss encounter")
        return
    end
    outf("active timeline events: %d", #list)
    for i = 1, math.min(#list, 5) do
        local id = list[i]
        outf("  event[%d] id=%s", i, classify(id))
        if not isSecret(id) then
            local _, remaining = try(C_EncounterTimeline.GetEventTimeRemaining, id)
            outf("    GetEventTimeRemaining=%s", classify(remaining))
            local okT, timer = try(C_EncounterTimeline.GetEventTimer, id)
            if okT and timer then
                outf("    GetEventTimer:HasSecretValues=%s", classify(select(2, try(timer.HasSecretValues, timer))))
            end
            local okI, info = try(C_EncounterTimeline.GetEventInfo, id)
            if okI and info then dumpFields("    info", info, "    ") end
        end
    end
end)

-------------------------------------------------------------------------------
addTest("fp", "Unit fingerprint oracles (requires a target)", function()
    if not UnitExists("target") then
        result("SKIP", "fingerprint", "no target — target a mob first")
        return
    end
    local probes = {
        { "UnitName",            function() return UnitName("target") end },
        { "UnitGUID",            function() return UnitGUID("target") end },
        { "UnitCreatureType",    function() return UnitCreatureType("target") end },
        { "UnitClassification",  function() return UnitClassification("target") end },
        { "UnitLevel",           function() return UnitLevel("target") end },
        { "UnitEffectiveLevel",  function() return UnitEffectiveLevel and UnitEffectiveLevel("target") end },
        { "UnitReaction",        function() return UnitReaction("target", "player") end },
        { "UnitSex",             function() return UnitSex("target") end },
        { "UnitPowerBarID",      function() return UnitPowerBarID and UnitPowerBarID("target") end },
        { "UnitWidgetSet",       function() return UnitWidgetSet and UnitWidgetSet("target") end },
        { "UnitIsBossMob",       function() return UnitIsBossMob and UnitIsBossMob("target") end },
        { "ThreatSituation",     function() return UnitThreatSituation("player", "target") end },
        { "UnitHealth",          function() return UnitHealth("target") end },
    }
    for _, probe in ipairs(probes) do
        local ok, v = try(probe[2])
        outf("  %-18s = %s", probe[1], ok and classify(v) or ("ERR " .. tostring(v)))
    end

    local okP, pt, ptToken, r, g, b = try(UnitPowerType, "target")
    if okP then
        outf("  UnitPowerType      = %s token=%s rgb=(%s,%s,%s)",
            classify(pt), classify(ptToken), classify(r), classify(g), classify(b))
    end

    -- UnitHasPowerType sweep: the predicate exempts "unit doesn't have this power"
    if UnitHasPowerType and Enum.PowerType then
        local names, has, secretN, errN = {}, {}, 0, 0
        for name, value in pairs(Enum.PowerType) do
            if type(value) == "number" and value >= 0 and value <= 30 then
                names[#names + 1] = { name = name, value = value }
            end
        end
        table.sort(names, function(a, b) return a.value < b.value end)
        for _, e in ipairs(names) do
            local ok, v = try(UnitHasPowerType, "target", e.value)
            if not ok then errN = errN + 1
            elseif isSecret(v) then secretN = secretN + 1
            elseif v then has[#has + 1] = e.name end
        end
        result(errN == 0 and secretN == 0 and "PASS" or (secretN > 0 and "SECRET" or "ERR"),
            "UnitHasPowerType sweep",
            format("has={%s} secret=%d err=%d of %d", table.concat(has, ","), secretN, errN, #names))
    else
        result("SKIP", "UnitHasPowerType sweep", "API or enum missing")
    end

    if C_Secrets and C_Secrets.CanCompareUnitTokens then
        for _, pair in ipairs({ { "target", "nameplate1" }, { "player", "target" }, { "party1", "nameplate1" } }) do
            local ok, v = try(C_Secrets.CanCompareUnitTokens, pair[1], pair[2])
            outf("  CanCompareUnitTokens(%s,%s) = %s", pair[1], pair[2], ok and classify(v) or "ERR")
        end
    end
end)

-------------------------------------------------------------------------------
addTest("allies", "Ally aura readability (externals whitelist)", function()
    local units = groupUnits(10)
    if #units == 0 then
        units = { "player" }
        emit("not in a group — running on player as baseline; re-run in a dungeon group")
    end
    if not (C_UnitAuras and C_UnitAuras.GetAuraDataByIndex) then
        result("FAIL", "GetAuraDataByIndex", "API missing")
        return
    end
    -- known aura=NeverSecret spells (from T7): do they stay readable in combat?
    local neverSecret = { [124682] = "Enveloping Mist", [115175] = "Soothing Mist" }
    local nsHits = {}
    for _, unit in ipairs(units) do
        if UnitExists(unit) then
            local readable, secretN, total = {}, 0, 0
            for i = 1, 60 do
                local ok, aura = try(C_UnitAuras.GetAuraDataByIndex, unit, i, "HELPFUL")
                if not ok or not aura then break end
                total = total + 1
                local okId, spellId = pcall(function() return aura.spellId end)
                if okId and spellId ~= nil and not isSecret(spellId) then
                    -- annotate with the declared secrecy level: if a readable
                    -- aura reports Contextual (not Never), readability is
                    -- caster-relative, not whitelist-driven
                    local tagTxt = tostring(spellId)
                    if C_Secrets and C_Secrets.GetSpellAuraSecrecy then
                        local okS, lvl = try(C_Secrets.GetSpellAuraSecrecy, spellId)
                        if okS and lvl ~= nil then
                            local names = { [0] = "never", [1] = "always", [2] = "ctx" }
                            tagTxt = format("%d[%s]", spellId, names[lvl] or tostring(lvl))
                        end
                    end
                    readable[#readable + 1] = tagTxt
                    if neverSecret[spellId] then
                        nsHits[#nsHits + 1] = format("%s (%d) on %s", neverSecret[spellId], spellId, unit)
                    end
                else
                    secretN = secretN + 1
                end
            end
            outf("  %s: %d auras, %d readable spellIds, %d secret", unit, total, total - secretN, secretN)
            if #readable > 0 then
                outf("    readable: %s", table.concat(readable, ",", 1, math.min(#readable, 15)))
            end
        end
    end
    if #nsHits > 0 then
        result("PASS", "NeverSecret whitelist", "readable in this context: " .. table.concat(nsHits, "; "))
    else
        emit("  NeverSecret probe: no whitelisted aura (Enveloping/Soothing Mist) found readable —")
        emit("  if one was ACTIVE on a scanned unit just now, the aura=NeverSecret flag does NOT survive this context")
    end
end)

-------------------------------------------------------------------------------
addTest("sweep", "C_Secrets per-spell secrecy sweep", function()
    if not (C_Secrets and C_Secrets.GetSpellAuraSecrecy) then
        result("FAIL", "C_Secrets.GetSpellAuraSecrecy", "API missing")
        return
    end
    local function sweepOne(spellID, name)
        local _, aura = try(C_Secrets.GetSpellAuraSecrecy, spellID)
        local _, cd   = try(C_Secrets.GetSpellCooldownSecrecy, spellID)
        local _, cast = try(C_Secrets.GetSpellCastSecrecy, spellID)
        outf("  %6d %-24s aura=%-10s cd=%-10s cast=%s",
            spellID, name or "?", secrecyLabel(aura), secrecyLabel(cd), secrecyLabel(cast))
    end

    emit("your CooldownViewer spells:")
    for _, spellID in ipairs(collectCDMSpells(20)) do
        local _, name = try(C_Spell.GetSpellName, spellID)
        sweepOne(spellID, type(name) == "string" and name or "?")
    end
    emit("common externals/raid CDs:")
    for _, entry in ipairs(EXTERNAL_SPELLS) do
        sweepOne(entry[1], entry[2])
    end
end)

-------------------------------------------------------------------------------
addTest("cds", "Own cooldown field secrecy", function()
    if not (C_Spell and C_Spell.GetSpellCooldown) then
        result("FAIL", "C_Spell.GetSpellCooldown", "API missing")
        return
    end
    for _, spellID in ipairs(collectCDMSpells(4)) do
        local _, name = try(C_Spell.GetSpellName, spellID)
        local ok, info = try(C_Spell.GetSpellCooldown, spellID)
        if ok and type(info) == "table" then
            outf("  %d %s:", spellID, type(name) == "string" and name or "?")
            local get = function(k) local o, v = pcall(function() return info[k] end) return o and classify(v) or "ERR" end
            outf("    startTime=%s duration=%s isEnabled=%s isActive=%s isOnGCD=%s",
                get("startTime"), get("duration"), get("isEnabled"), get("isActive"), get("isOnGCD"))
        else
            result("ERR", "GetSpellCooldown " .. spellID, tostring(info))
        end
        local ok2, charges = try(C_Spell.GetSpellCharges, spellID)
        if ok2 and type(charges) == "table" then
            local get = function(k) local o, v = pcall(function() return charges[k] end) return o and classify(v) or "ERR" end
            outf("    charges: current=%s max=%s isActive=%s",
                get("currentCharges"), get("maxCharges"), get("isActive"))
        end
    end
end)

-------------------------------------------------------------------------------
addTest("env", "Secret-value operational envelope", function()
    -- hunt for a secret value in the current context
    local secretVal, source
    local candidates = {
        { "UnitHealth(target)",  function() return UnitHealth("target") end },
        { "UnitName(target)",    function() return UnitName("target") end },
        { "UnitGUID(target)",    function() return UnitGUID("target") end },
        { "GetRaidTargetIndex(target)", function() return GetRaidTargetIndex and GetRaidTargetIndex("target") end },
    }
    for _, c in ipairs(candidates) do
        local ok, v = try(c[2])
        if ok and isSecret(v) then secretVal, source = v, c[1] break end
    end
    -- fall back to aura fields on target/party
    if secretVal == nil and C_UnitAuras and C_UnitAuras.GetAuraDataByIndex then
        for _, unit in ipairs({ "target", "party1", "player" }) do
            if UnitExists(unit) then
                for i = 1, 40 do
                    local ok, aura = try(C_UnitAuras.GetAuraDataByIndex, unit, i, "HELPFUL")
                    if not ok or not aura then break end
                    local okF, v = pcall(function() return aura.spellId end)
                    if okF and isSecret(v) then
                        secretVal, source = v, format("aura.spellId(%s#%d)", unit, i)
                        break
                    end
                end
            end
            if secretVal then break end
        end
    end
    if secretVal == nil then
        result("SKIP", "envelope", "no secret value found here — re-run in an instance/combat")
        return
    end
    outf("probing secret from %s (type=%s)", source, select(2, pcall(type, secretVal)) or "?")

    local ops = {
        { "type(s)",        function() return type(secretVal) end },
        { "s == true",      function() return secretVal == true end },
        { "s == s",         function() return secretVal == secretVal end },
        { "not s",          function() return not secretVal end },
        { "s and 1",        function() return secretVal and 1 end },
        { "if s then",      function() if secretVal then return 1 else return 0 end end },
        { "tostring(s)",    function() return tostring(secretVal) end },
        { "s .. \"\"",      function() return secretVal .. "" end },
        { "s + 0",          function() return secretVal + 0 end },
        { "t[s] = 1",       function() local t = {} t[secretVal] = 1 return true end },
        { "issecretvalue",  function() return issecretvalue(secretVal) end },
        { "SetText(s)",     function() getSinks().fs:SetText(secretVal) return "ok" end },
        { "SetValue(s)",    function() local b = getSinks().bar b:SetMinMaxValues(0, 2^31) b:SetValue(secretVal) return "ok" end },
        { "SetShown(s)",    function() getSinks().tex:SetShown(secretVal) return "ok" end },
        { "SetAlphaFromBoolean(s)", function()
            local t = getSinks().tex
            if not t.SetAlphaFromBoolean then return "method missing" end
            t:SetAlphaFromBoolean(secretVal, 255, 0) return "ok"
        end },
    }
    for _, op in ipairs(ops) do
        local ok, r = pcall(op[2])
        if ok then
            outf("  %-24s -> ok (%s)", op[1], classify(r))
        else
            outf("  %-24s -> ERROR: %s", op[1], tostring(r):sub(1, 90))
        end
    end
    resetSink(getSinks().fs); resetSink(getSinks().bar); resetSink(getSinks().tex)
end)

-------------------------------------------------------------------------------
addTest("aspects", "Blizzard frame secret-aspect sweep", function()
    local targets = {
        { "TargetFrame healthbar", function()
            local tf = TargetFrame
            return tf and tf.TargetFrameContent and tf.TargetFrameContent.TargetFrameContentMain
                and tf.TargetFrameContent.TargetFrameContentMain.HealthBarsContainer
                and tf.TargetFrameContent.TargetFrameContentMain.HealthBarsContainer.HealthBar
        end },
    }
    for i = 1, 5 do
        targets[#targets + 1] = { "Boss" .. i .. "TargetFrame.HealthBar", function()
            local f = _G["Boss" .. i .. "TargetFrame"]
            return f and (f.HealthBar or (f.TargetFrameContent and f.TargetFrameContent.TargetFrameContentMain
                and f.TargetFrameContent.TargetFrameContentMain.HealthBarsContainer
                and f.TargetFrameContent.TargetFrameContentMain.HealthBarsContainer.HealthBar))
        end }
    end
    local found = 0
    for _, t in ipairs(targets) do
        local ok, bar = pcall(t[2])
        if ok and bar and bar:IsVisible() then
            found = found + 1
            local _, val = try(bar.GetValue, bar)
            local aspectInfo = "n/a"
            if bar.HasAnySecretAspect then
                local okA, a = try(bar.HasAnySecretAspect, bar)
                aspectInfo = okA and classify(a) or "ERR"
            end
            outf("  %s: GetValue=%s HasAnySecretAspect=%s", t[1], classify(val), aspectInfo)
        end
    end
    if found == 0 then
        result("INFO", "aspect sweep", "no visible target/boss health bars — target something / fight a boss")
    end
end)

-------------------------------------------------------------------------------
addTest("hp", "Health readability & UnitHealthPercent side-channel", function()
    local units = mobUnits()
    if #units == 0 then
        result("SKIP", "hp", "no attackable target/boss/nameplate — target a mob")
        return
    end
    for _, unit in ipairs(units) do
        outf("  %s: UnitHealth=%s UnitHealthMax=%s", unit,
            classify(select(2, try(UnitHealth, unit))),
            classify(select(2, try(UnitHealthMax, unit))))
        -- UnitHealthPercent(unit, true): SecretWhenCurveSecret, doc'd "for display
        -- purposes" — may be plain and readable, unlike UnitHealth.
        local okP, pct = try(UnitHealthPercent, unit, true)
        if okP then
            branchProbe("UnitHealthPercent(u,true)", pct)
            if not isSecret(pct) then
                local okC, r = pcall(function() return pct > 0.5 end)
                outf("      (pct>0.5) -> %s", okC and classify(r) or ("ERROR " .. tostring(r):sub(1, 50)))
            end
        else
            outf("    UnitHealthPercent(u,true) ERR: %s", tostring(pct):sub(1, 50))
        end
    end
    emit("  if UnitHealthPercent is plain+comparable -> simplify BossHealthTracker (drop hidden-bar trick)")
end)

-------------------------------------------------------------------------------
addTest("widgets", "UIWidget encounter-intel channel (all plain)", function()
    if not C_UIWidgetManager then result("FAIL", "widgets", "C_UIWidgetManager missing"); return end
    local sets = {}
    local function addSet(label, fn)
        local ok, id = try(fn)
        if ok and type(id) == "number" and id ~= 0 then sets[#sets + 1] = { label, id } end
    end
    addSet("TopCenter", C_UIWidgetManager.GetTopCenterWidgetSetID)
    addSet("BelowMinimap", C_UIWidgetManager.GetBelowMinimapWidgetSetID)
    addSet("PowerBar", C_UIWidgetManager.GetPowerBarWidgetSetID)
    addSet("ObjectiveTracker", C_UIWidgetManager.GetObjectiveTrackerWidgetSetID)
    if UnitWidgetSet then
        for _, unit in ipairs({ "target", "nameplate1", "boss1" }) do
            if UnitExists(unit) then
                local ok, id = try(UnitWidgetSet, unit)
                if ok and type(id) == "number" and id ~= 0 then sets[#sets + 1] = { "unit:" .. unit, id } end
            end
        end
    end
    if #sets == 0 then result("INFO", "widgets", "no active widget sets — run during an encounter"); return end
    for _, set in ipairs(sets) do
        local ok, widgets = try(C_UIWidgetManager.GetAllWidgetsBySetID, set[2])
        outf("  set %-16s id=%d -> %s widget(s)", set[1], set[2],
            (ok and type(widgets) == "table") and #widgets or "ERR")
        if ok and type(widgets) == "table" then
            for i = 1, math.min(#widgets, 4) do
                local w = widgets[i]
                outf("    [%d] type=%s id=%s tag=%s", i,
                    classify(w.widgetType), classify(w.widgetID), classify(w.widgetTag))
            end
        end
    end
    emit("  every field plain -> generic boss energy / add-count / phase mirror without per-encounter code")
end)

-------------------------------------------------------------------------------
addTest("catalog", "C_EncounterEvents static warning catalog", function()
    if not (C_EncounterEvents and C_EncounterEvents.GetEventList) then
        result("SKIP", "catalog", "C_EncounterEvents missing"); return
    end
    local ok, list = try(C_EncounterEvents.GetEventList)
    if not ok or type(list) ~= "table" then result("ERR", "GetEventList", tostring(list)); return end
    outf("  catalog: %d encounter event(s) (global, not encounterID-linked)", #list)
    for i = 1, math.min(#list, 6) do
        local okI, info = try(C_EncounterEvents.GetEventInfo, list[i])
        if okI and type(info) == "table" then
            outf("    id=%s spellID=%s icon=%s severity=%s icons=%s enabled=%s",
                classify(info.encounterEventID), classify(info.spellID), classify(info.iconFileID),
                classify(info.severity), classify(info.icons), classify(info.enabled))
        end
    end
    emit("  all plain -> pre-configure per-ability warning colors/sounds offline by spellID (fire-and-forget)")
end)

-------------------------------------------------------------------------------
addTest("dr", "C_SpellDiminish (DR tracker feasibility)", function()
    if not C_SpellDiminish then result("SKIP", "dr", "C_SpellDiminish missing"); return end
    outf("  IsSystemSupported=%s", classify(select(2, try(C_SpellDiminish.IsSystemSupported))))
    for _, rs in ipairs({ "PvP", "PvE" }) do
        local rv = Enum.SpellDiminishRuleset and Enum.SpellDiminishRuleset[rs]
        local ok, cats = try(C_SpellDiminish.GetAllSpellDiminishCategories, rv)
        outf("  GetAllSpellDiminishCategories(%s) = %s", rs,
            (ok and type(cats) == "table") and (#cats .. " categories") or classify(cats))
    end
    if Enum.SpellDiminishCategory and Enum.SpellDiminishRuleset then
        local ok, tracked = try(C_SpellDiminish.ShouldTrackSpellDiminishCategory,
            Enum.SpellDiminishCategory.Taunt, Enum.SpellDiminishRuleset.PvE)
        outf("  ShouldTrackSpellDiminishCategory(Taunt,PvE) = %s (docs: SecretReturns=true)",
            ok and classify(tracked) or ("ERR " .. tostring(tracked):sub(1, 40)))
    end
    emit("  live per-unit state = UNIT_SPELL_DIMINISH_CATEGORY_STATE_UPDATED (SecretPayloads) — probe in arena")
end)

-------------------------------------------------------------------------------
-- Commands
-------------------------------------------------------------------------------
local function listProbes(includeHidden)
    header("Probes")
    for _, key in ipairs(probeOrder) do
        local probe = probes[key]
        if includeHidden or not probe.hidden then
            outf("  %s %s%s", ns.Pad(key, 12), probe.title,
                probe.manual and "  (manual)" or "")
        end
    end
    emit("  /euidiag secrets <key> to run one, /euidiag secrets for all of them")
end

local function runAllProbes()
    emit(("="):rep(60))
    outf("secret-value sweep — %s", date("%Y-%m-%d %H:%M:%S"))
    local inInstance, instanceType = IsInInstance()
    outf("context: %s / %s, combat=%s, group=%s",
        tostring(instanceType), tostring(inInstance and "instance" or "world"),
        tostring(InCombatLockdown()),
        IsInRaid() and "raid" or IsInGroup() and "party" or "solo")
    local ran = 0
    for _, key in ipairs(probeOrder) do
        local probe = probes[key]
        if not probe.manual and not probe.hidden then
            ns.RunProbe(key)
            ran = ran + 1
        end
    end
    outf("%d probe(s) run. /euidiag copy for a paste-friendly log", ran)
end

ns.Command("secrets", {
    group = "Secrets",
    usage = "secrets [list [all] | <key>]",
    help  = "probe what is secret in the current context",
    fn    = function(args)
        local sub = args[1]
        if not sub then return runAllProbes() end
        if sub:lower() == "list" then return listProbes(args[2] == "all") end
        if not ns.RunProbe(sub) then
            outf("no probe '%s' — /euidiag secrets list", sub)
        end
    end,
})

-------------------------------------------------------------------------------
-- /euidiag eval
-------------------------------------------------------------------------------
-- The general form of every probe above: point it at anything and it reports
-- what came back without ever performing an operation a secret would refuse.
-- `/euidiag eval UnitHealth("target")` beats writing a probe for a one-off
-- question, and it is the fastest way to check whether an API the suite is
-- about to depend on returns something readable in the context you are in.
ns.Command("eval", {
    group = "Secrets",
    usage = "eval <lua expression>",
    help  = "run an expression and classify everything it returns",
    fn    = function(args, raw)
        if raw == "" then
            emit("usage: /euidiag eval UnitHealth(\"target\")")
            return
        end
        local chunk, compileErr = loadstring("return " .. raw, "euidiag-eval")
        if not chunk then
            -- Not an expression, so try it as a statement block: `eval` is also
            -- a convenient way to poke at state, not only to read it.
            chunk, compileErr = loadstring(raw, "euidiag-eval")
        end
        if not chunk then
            result("ERR", "eval", tostring(compileErr))
            return
        end
        local returns = { pcall(chunk) }
        if not returns[1] then
            result("ERR", "eval", tostring(returns[2]))
            return
        end
        outf("eval: %s", raw)
        if #returns == 1 then
            result("INFO", "returned", "nothing")
            return
        end
        for i = 2, #returns do
            local value = returns[i]
            outf("  [%d] %s", i - 1, classify(value))
            -- A table is usually the interesting case, and its fields are where
            -- the secrets hide — GetSearchResultInfo is plain, its .name is not.
            if type(value) == "table" and not isSecret(value) then
                dumpFields(format("[%d] fields", i - 1), value, "  ")
            end
        end
    end,
})

-------------------------------------------------------------------------------
-- /euidiag aurarows
-------------------------------------------------------------------------------
-- Blizzard documents a filter token in one sentence; what it admits only shows
-- up live. Run several side by side and see which populate.
--
-- READ IT VISUALLY: group counts are obfuscated and a tainted addon cannot
-- enumerate what it just displayed, so the icons ARE the result.
local AURA_ROW_PRESETS = {
    -- Each row is { label, { filter tokens }, candidateFilters or nil }.
    --
    -- What the dispel-glow fix turns on: does DISPELLABLE admit an enrage, and
    -- do the two dispel-type filters partition the row exactly? Rows 4 and 5
    -- are the answer -- an enrage has no dispelName, so include rejects it and
    -- exclude keeps it.
    dispel = {
        { "1 base (all buffs)", { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY" } },
        { "2 DISPELLABLE",      { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY", "DISPELLABLE" } },
        { "3 RAID_PLAYER_DISP", { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY", "RAID_PLAYER_DISPELLABLE" } },
        { "4 DISP  +Magic",     { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY", "DISPELLABLE" },
            { includeDispelTypes = { Magic = true } } },
        { "5 DISP  -Magic",     { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY", "DISPELLABLE" },
            { excludeDispelTypes = { Magic = true } } },
        { "6 RAIDP -Magic",     { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY", "RAID_PLAYER_DISPELLABLE" },
            { excludeDispelTypes = { Magic = true } } },
    },
    helpful = {
        { "1 base",             { "HELPFUL" } },
        { "2 DISPELLABLE",      { "HELPFUL", "DISPELLABLE" } },
        { "3 RAID_PLAYER_DISP", { "HELPFUL", "RAID_PLAYER_DISPELLABLE" } },
        { "4 IMPORTANT",        { "HELPFUL", "IMPORTANT" } },
        { "5 nameplate only",   { "HELPFUL", "INCLUDE_NAME_PLATE_ONLY" } },
    },
    harmful = {
        { "1 base",           { "HARMFUL" } },
        { "2 mine",           { "HARMFUL", "PLAYER" } },
        { "3 CROWD_CONTROL",  { "HARMFUL", "CROWD_CONTROL" } },
        { "4 not CC",         { "HARMFUL", "!CROWD_CONTROL" } },
        { "5 DISPELLABLE",    { "HARMFUL", "DISPELLABLE" } },
    },
}

local rowPanel, rowContainers, rowSizeDenied = nil, {}, 0

-- An UNSIZED BUTTON RENDERS NOTHING: the flow layout only ANCHORS group buttons,
-- and the group layout's elementWidth/elementHeight feed the flow math alone.
-- SetSize is also denied while auras are secret, so build rows BEFORE entering
-- restricted content -- rowSizeDenied reports when that bit.
local function InitRowButton(button)
    if not pcall(button.SetSize, button, 26, 26) then
        rowSizeDenied = rowSizeDenied + 1
    end
    -- A flat backing behind the icon, so a button the engine populated still
    -- reads even if the icon texture never gets painted. This is what tells
    -- "no matching aura" apart from "matched but drew nothing".
    local bg = button:CreateTexture(nil, "BACKGROUND")
    bg:SetAllPoints(button)
    bg:SetColorTexture(0.25, 0.25, 0.9, 0.9)

    local tex = button:CreateTexture(nil, "ARTWORK")
    tex:SetAllPoints(button)
    pcall(button.SetIcon, button, tex)
end

local function RowsSetUnit(unit)
    for i = 1, #rowContainers do
        pcall(function()
            rowContainers[i]:SetUnit(unit)
            rowContainers[i]:UpdateAllAuras()
        end)
    end
end

local rowWatcher
local function BuildAuraRows(rows, unit)
    if rowPanel then rowPanel:Hide() end
    rowPanel, rowContainers, rowSizeDenied = nil, {}, 0

    if not C_AddOns.IsAddOnLoaded("Blizzard_AuraContainer") then
        C_AddOns.LoadAddOn("Blizzard_AuraContainer")
    end

    rowPanel = CreateFrame("Frame", "EUIDiagAuraRowsPanel", UIParent, "BackdropTemplate")
    rowPanel:SetSize(430, 40 + #rows * 34)
    rowPanel:SetPoint("CENTER", UIParent, "CENTER", 0, 150)
    rowPanel:SetMovable(true)
    rowPanel:EnableMouse(true)
    rowPanel:RegisterForDrag("LeftButton")
    rowPanel:SetScript("OnDragStart", rowPanel.StartMoving)
    rowPanel:SetScript("OnDragStop", rowPanel.StopMovingOrSizing)
    if rowPanel.SetBackdrop then
        rowPanel:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
        rowPanel:SetBackdropColor(0, 0, 0, 0.75)
    end

    local title = rowPanel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    title:SetPoint("TOP", rowPanel, "TOP", 0, -8)
    title:SetText("aurarows: " .. unit .. " (drag to move)")

    for i = 1, #rows do
        local label, tokens, cand = rows[i][1], rows[i][2], rows[i][3]
        local y = -28 - (i - 1) * 34

        local fs = rowPanel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        fs:SetPoint("TOPLEFT", rowPanel, "TOPLEFT", 10, y - 6)
        fs:SetWidth(180)
        fs:SetJustifyH("LEFT")
        fs:SetText(label)

        local host = CreateFrame("Frame", nil, rowPanel)
        host:SetSize(220, 28)
        host:SetPoint("TOPLEFT", rowPanel, "TOPLEFT", 196, y)

        local ok, err = pcall(function()
            local c = CreateFrame("AuraContainer", nil, host, "CustomAuraContainerTemplate")
            c:SetPoint("LEFT", host, "LEFT")
            c:SetSize(220, 28)
            c:AddAuraGroup("probe", table.concat(tokens, "|"), {
                maxFrameCount = 8,
                candidateFilters = cand,
                initializeFrame = InitRowButton,
                layout = { elementWidth = 26, elementHeight = 26,
                    elementSpacing = 3, lineSpacing = 3 },
            })
            -- Unit LAST: SetUnit re-evaluates event registration, and that is
            -- gated on the container already having groups.
            c:SetUnit(unit)
            c:UpdateAllAuras()
            rowContainers[#rowContainers + 1] = c
        end)
        if not ok then
            fs:SetText(label .. " |cffff4040FAILED|r")
            result("ERR", label, tostring(err))
        end
    end

    if not rowWatcher then
        rowWatcher = CreateFrame("Frame")
        rowWatcher:SetScript("OnEvent", function()
            if rowPanel and rowPanel:IsShown() then RowsSetUnit(rowPanel._unit or "target") end
        end)
    end
    rowPanel._unit = unit
    rowWatcher:UnregisterAllEvents()
    if unit == "target" then rowWatcher:RegisterEvent("PLAYER_TARGET_CHANGED") end
    rowWatcher:RegisterEvent("UNIT_AURA")
end

ns.Command("aurarows", {
    group = "Secrets",
    usage = "aurarows [dispel|helpful|harmful|custom] [unit] | off | list",
    help  = "bench engine aura filters side by side; read the panel, not a count",
    fn    = function(args)
        local sub = (args[1] or "dispel"):lower()
        if sub == "off" then
            if rowPanel then rowPanel:Hide() end
            RowsSetUnit("none")
            emit("aurarows: hidden")
            return
        end
        if sub == "list" then
            for name, rows in pairs(AURA_ROW_PRESETS) do
                outf("  %s (%d rows)", name, #rows)
            end
            emit("custom: set EUIDiagAuraRows = { {label, {tokens}, cand}, ... }")
            return
        end
        local rows = AURA_ROW_PRESETS[sub]
        if sub == "custom" then rows = _G.EUIDiagAuraRows end
        if type(rows) ~= "table" or #rows == 0 then
            outf("no preset '%s' -- /euidiag aurarows list", sub)
            return
        end
        local unit = args[2] or "target"
        local ok, err = pcall(BuildAuraRows, rows, unit)
        if not ok then
            result("ERR", "aurarows", tostring(err))
            return
        end
        result("INFO", "aurarows", format("%s, %d rows on %s", sub, #rows, unit))
        -- Auras go secret on entering restricted content, and a button sized
        -- after that point stays invisible. Say so rather than let the panel
        -- read as "nothing matched".
        if rowSizeDenied > 0 then
            result("WARN", "SetSize denied",
                format("%d buttons -- rebuild OUTSIDE restricted content", rowSizeDenied))
        end
        emit("read the icons, not a count: group counts are obfuscated")
    end,
})
