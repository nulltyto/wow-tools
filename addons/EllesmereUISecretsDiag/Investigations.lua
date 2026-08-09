-------------------------------------------------------------------------------
--  Investigations.lua  —  parked, question-specific probe batteries
--
--  Everything here was built to answer one question about one change, and each
--  one answered it. They are kept because the protocols are hard-won and the
--  findings are the reason parts of the suite are shaped the way they are —
--  not because they are part of the everyday tool.
--
--  Nothing in this file is listed by `/euidiag help` or run by
--  `/euidiag secrets`. Everything is still reachable:
--
--    /euidiag secrets list all   includes the parked probes
--    /euidiag secrets <key>      runs one of them
--    /euidiag help all           lists the parked commands
--
--  What is in here, and what it settled:
--
--    dead, classify, icon, portrait, widthfp, threat, reveal, ping
--        The 2026-07-07 inference-trick battery. Each hunts a way to recover
--        information that secret values are meant to hide — classification
--        laundering, icon and portrait identity, text-width oracles, tooltip
--        reveal. Run as a set with `/euidiag round2`.
--
--    lfg, secureclick, preclick, prime
--        The Group Finder taint investigation. It established that
--        SecureActionButtonTemplate click-forwarding runs the delegate
--        untainted, which is what makes an addon-driven premade filter
--        possible at all. Each carries its own step-by-step protocol in the
--        comments above it — taint is sticky, so the order genuinely matters
--        and a run that skips the /reload proves nothing.
--
--    casts, deathwatch, inject, hunt
--        Event-stream observers and one spell-ID sweep. `hunt` walks every
--        spell ID looking for the NeverSecret whitelist and takes a while.
--
--  If one of these turns out to be generally useful, that is a signal to
--  rewrite it as a real tool in Taint.lua or Secrets.lua rather than to
--  promote it from here — these are shaped around single answers, and their
--  hardcoded frame paths and field lists will not survive contact with the
--  next question.
-------------------------------------------------------------------------------
local ADDON_NAME, ns = ...

local format, floor = string.format, math.floor
local emit, outf, header, result = ns.emit, ns.outf, ns.header, ns.result
local classify, isSecret, try = ns.classify, ns.isSecret, ns.try
local dumpFields = ns.dumpFields
local Diag = ns.addon

-- Shared helpers, defined once in Secrets.lua.
local groupUnits = ns.groupUnits
local existingUnits = ns.existingUnits
local mobUnits = ns.mobUnits
local findSecretAuraSpellId = ns.findSecretAuraSpellId
local collectCDMSpells = ns.collectCDMSpells
local getSinks, resetSink = ns.getSinks, ns.resetSink
local branchProbe = ns.branchProbe

-- Everything registered from this file is hidden: reachable by key, never part
-- of a `/euidiag secrets` sweep.
local function addTest(key, title, fn)
    ns.Probe(key, title, fn, { manual = true, hidden = true })
end

-------------------------------------------------------------------------------
--  Frames reused across the probes below
-------------------------------------------------------------------------------
-- Module-level frames reused across probe runs.
local portraitTex, portraitModel, revealTip

-------------------------------------------------------------------------------
--  T21 UnitIsDead death oracle
-------------------------------------------------------------------------------
addTest("dead", "T21: UnitIsDead death oracle on hostile units", function()
    local units = mobUnits()
    if #units == 0 then
        result("SKIP", "dead", "no attackable mobs present")
        return
    end
    for _, unit in ipairs(units) do
        outf("  %-11s IsDead=%s DeadOrGhost=%s Corpse=%s FeignDeath=%s AffectingCombat=%s", unit,
            classify(select(2, try(UnitIsDead, unit))),
            classify(select(2, try(UnitIsDeadOrGhost, unit))),
            classify(select(2, try(UnitIsCorpse, unit))),
            classify(select(2, try(UnitIsFeignDeath, unit))),
            classify(select(2, try(UnitAffectingCombat, unit))))
    end
    emit("  plain bool on a hostile mob here = CLEU-free mob-death detection (highest payoff)")
end)

-------------------------------------------------------------------------------
--  T22-T27 inference-trick battery
-------------------------------------------------------------------------------
addTest("classify", "T22: Classification laundering (secret spellID -> plain class)", function()
    -- baseline: static classifiers on a KNOWN plain spellID must return sanely
    local baseID = 33206 -- Pain Suppression
    outf("  baseline plain spellID %d (Pain Suppression):", baseID)
    outf("    AuraIsBigDefensive=%s IsSpellCrowdControl=%s DeadlyDebuffInfo=%s",
        classify(select(2, try(C_UnitAuras and C_UnitAuras.AuraIsBigDefensive, baseID))),
        classify(select(2, try(C_Spell and C_Spell.IsSpellCrowdControl, baseID))),
        classify(select(2, try(C_Spell and C_Spell.GetDeadlyDebuffInfo, baseID))))

    local secretID, src = findSecretAuraSpellId()
    if secretID == nil then
        result("INFO", "laundering", "no secret aura spellID found — re-run in-instance in combat")
        return
    end
    outf("  secret spellID from %s -> classify WITHOUT reading it:", src)
    local function launder(name, fn)
        if type(fn) ~= "function" then outf("    %-22s = API missing", name); return end
        local ok, r = pcall(fn, secretID)
        if not ok then outf("    %-22s -> ERROR: %s", name, tostring(r):sub(1, 55)); return end
        -- branch test: a plain result is usable; a secret bool errors on `if`
        local okB = pcall(function() if r then return 1 end return 0 end)
        outf("    %-22s = %s (branch %s)", name, classify(r), okB and "ok" or "ERRORS")
    end
    launder("AuraIsBigDefensive", C_UnitAuras and C_UnitAuras.AuraIsBigDefensive)
    launder("AuraIsPrivate", C_UnitAuras and C_UnitAuras.AuraIsPrivate)
    launder("IsSpellCrowdControl", C_Spell and C_Spell.IsSpellCrowdControl)
    launder("GetDeadlyDebuffInfo", C_Spell and C_Spell.GetDeadlyDebuffInfo)
    launder("IsExternalDefensive", C_Spell and C_Spell.IsExternalDefensive) -- AllowedWhenUntainted: may ERROR
    emit("  plain+branchable returns here = anonymous ally auras become CLASSIFIED (externals/CC/deadly)")
end)

-------------------------------------------------------------------------------
addTest("icon", "T23: Icon-fileID readback (plain while name is secret)", function()
    if not (C_UnitAuras and C_UnitAuras.GetAuraDataByIndex) then
        result("FAIL", "icon", "GetAuraDataByIndex missing"); return
    end
    local units = {}
    if UnitExists("target") then units[#units + 1] = "target" end
    for _, u in ipairs(groupUnits(3)) do units[#units + 1] = u end
    for _, u in ipairs(existingUnits("nameplate", 3)) do units[#units + 1] = u end
    local shown, plainWhileSecret = 0, 0
    for _, unit in ipairs(units) do
        for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
            for i = 1, 20 do
                local ok, aura = try(C_UnitAuras.GetAuraDataByIndex, unit, i, filter)
                if not ok or not aura then break end
                local sid = (pcall(function() return aura.spellId end)) and aura.spellId or nil
                local icon = (pcall(function() return aura.icon end)) and aura.icon or nil
                if isSecret(sid) and icon ~= nil and not isSecret(icon) then
                    plainWhileSecret = plainWhileSecret + 1
                    if shown < 12 then
                        outf("  %s %s#%d: spellId=%s icon=%s  <- NAME SECRET / ICON PLAIN",
                            unit, filter, i, classify(sid), classify(icon))
                        shown = shown + 1
                    end
                end
            end
        end
    end
    -- nameplate castbar icon fileID (only if a nameplate is mid-cast)
    for _, unit in ipairs(existingUnits("nameplate", 8)) do
        local okN, np = try(C_NamePlate and C_NamePlate.GetNamePlateForUnit, unit)
        local uf = okN and np and np.UnitFrame
        local cb = uf and (uf.castBar or uf.CastBar)
        local ic = cb and (cb.Icon or cb.icon)
        if ic and ic.GetTextureFileID then
            outf("  %s castbar icon fileID=%s", unit, classify(select(2, try(ic.GetTextureFileID, ic))))
        end
    end
    result(plainWhileSecret > 0 and "PASS" or "INFO", "icon-while-name-secret",
        format("%d aura(s) had plain icon + secret spellId", plainWhileSecret))
    emit("  plain icon fileID -> reverse-lookup a local spellID<->icon table to ID a secret-named aura")
end)

-------------------------------------------------------------------------------
addTest("portrait", "T24: Portrait / displayID identity vectors", function()
    local holder = getSinks().holder
    if not portraitTex then portraitTex = holder:CreateTexture(nil, "ARTWORK") end
    local units = mobUnits()
    if #units == 0 then result("SKIP", "portrait", "no attackable mobs"); return end

    -- 2D portrait: SetPortraitTexture(tex, unit) -> GetTextureFileID (the one
    -- architecturally-open vector). Compare fileIDs across DIFFERENT mobs: if
    -- they vary by species it's creature-unique; if constant it's a generic
    -- fallback (identifies nothing).
    outf("  2D SetPortraitTexture -> GetTextureFileID (compare across mobs for uniqueness):")
    for _, unit in ipairs(units) do
        local okS = pcall(SetPortraitTexture, portraitTex, unit)
        if okS then
            local fid = select(2, try(portraitTex.GetTextureFileID, portraitTex))
            local okB = pcall(function() return fid == 123456 end)
            outf("    %-11s fileID=%s (==literal %s)", unit, classify(fid),
                okB and "ok/branchable" or "ERRORS")
        else
            outf("    %-11s SetPortraitTexture ERR", unit)
        end
    end

    -- 3D model: SetUnit is expected to THROW on a secret enemy
    -- (RequiresDeclassifiedUnitIdentity / ReturnWithError).
    if not portraitModel then
        portraitModel = CreateFrame("PlayerModel", nil, holder)
        portraitModel:SetSize(1, 1)
    end
    for _, unit in ipairs(units) do
        local okU, err = pcall(portraitModel.SetUnit, portraitModel, unit)
        if okU then
            outf("    3D SetUnit(%s) OK -> GetDisplayInfo=%s", unit,
                classify(select(2, try(portraitModel.GetDisplayInfo, portraitModel))))
        else
            outf("    3D SetUnit(%s) THREW (gated, as expected): %s", unit, tostring(err):sub(1, 45))
        end
    end
    emit("  usable only if 2D fileID differs by species AND the ==literal test says branchable")
end)

-------------------------------------------------------------------------------
addTest("widthfp", "T25: Geometry laundering oracles (text-width / fill-width)", function()
    local s = getSinks()
    local holder = s.holder
    holder:SetAlpha(0); holder:Show() -- geometry needs a shown frame; alpha 0 = invisible

    -- (a) FontString text-width after SetText(secret string)
    local secretID = findSecretAuraSpellId()
    if secretID ~= nil then
        resetSink(s.fs)
        local secretStr = (pcall(function() return tostring(secretID) end)) and tostring(secretID) or secretID
        local okSet = pcall(s.fs.SetText, s.fs, secretStr)
        if okSet then
            outf("  FontString after SetText(secret string):")
            outf("    HasAnySecretAspect=%s", classify(select(2, try(s.fs.HasAnySecretAspect, s.fs))))
            branchProbe("GetStringWidth", select(2, try(s.fs.GetStringWidth, s.fs)))
            branchProbe("GetUnboundedStringWidth", select(2, try(s.fs.GetUnboundedStringWidth, s.fs)))
            branchProbe("GetNumLines", select(2, try(s.fs.GetNumLines, s.fs)))
            branchProbe("IsTruncated", select(2, try(s.fs.IsTruncated, s.fs)))
        else
            outf("  SetText(secret) failed: %s", tostring(secretStr):sub(1, 40))
        end
        resetSink(s.fs)
    else
        emit("  (no secret string source for text-width probe — in-instance combat needed)")
    end

    -- (b) StatusBar fill-texture width after SetValue(secret number)
    local secretNum = select(2, try(UnitHealth, "target"))
    if isSecret(secretNum) then
        resetSink(s.bar)
        pcall(s.bar.SetMinMaxValues, s.bar, 0, 2 ^ 31)
        s.bar:SetWidth(200); s.bar:SetHeight(10)
        if pcall(s.bar.SetValue, s.bar, secretNum) then
            outf("  StatusBar after SetValue(secret), width=200:")
            outf("    HasAnySecretAspect=%s", classify(select(2, try(s.bar.HasAnySecretAspect, s.bar))))
            local tex = select(2, try(s.bar.GetStatusBarTexture, s.bar))
            if tex then branchProbe("fillTexture:GetWidth", select(2, try(tex.GetWidth, tex))) end
            branchProbe("StatusBar:GetValue", select(2, try(s.bar.GetValue, s.bar)))
        end
        resetSink(s.bar)
    else
        emit("  (target UnitHealth not secret here — target a mob in an instance for fill-width probe)")
    end

    holder:Hide()
    emit("  a PLAIN width that tracks the secret = a fingerprint/value oracle Blizzard left open")
end)

-------------------------------------------------------------------------------
addTest("threat", "T26: Threat matrix (ally-vs-mob, looser gate than identity)", function()
    local allies = { "player" }
    for _, u in ipairs(groupUnits(10)) do allies[#allies + 1] = u end
    local mobs = mobUnits()
    if #mobs == 0 then result("SKIP", "threat", "no mob units"); return end
    for _, mob in ipairs(mobs) do
        for _, ally in ipairs(allies) do
            local okS, sit = try(UnitThreatSituation, ally, mob)
            local okD, _, status, pct = try(UnitDetailedThreatSituation, ally, mob)
            outf("  %-8s vs %-11s Situation=%s status=%s pct=%s", ally, mob,
                okS and classify(sit) or "ERR",
                okD and classify(status) or "ERR", okD and classify(pct) or "ERR")
        end
    end
    emit("  plain in combat -> per-ally aggro meter (predicate exempts ally-vs-mob pairs)")
end)

-------------------------------------------------------------------------------
addTest("reveal", "T27: Tooltip identity reveal (SANCTIONED escape hatch)", function()
    if not revealTip then
        revealTip = CreateFrame("GameTooltip", "EUISecretsDiagTip", UIParent, "GameTooltipTemplate")
    end
    local function line1()
        local fs = _G["EUISecretsDiagTipTextLeft1"]
        if not fs then return "no line1" end
        return classify(select(2, try(fs.GetText, fs)))
    end
    revealTip:SetOwner(UIParent, "ANCHOR_NONE")
    revealTip:ClearAllPoints()
    revealTip:SetPoint("CENTER")

    if UnitExists("target") then
        local ok, err = pcall(revealTip.SetUnit, revealTip, "target")
        outf("  SetUnit('target') -> %s; line1=%s lines=%s",
            ok and "ok" or ("ERR " .. tostring(err):sub(1, 40)),
            line1(), classify(select(2, try(revealTip.NumLines, revealTip))))
    end
    -- reveal an ally's aura by plain instance ID (the anonymous-aura use case)
    if C_UnitAuras and C_UnitAuras.GetUnitAuraInstanceIDs then
        for _, unit in ipairs(groupUnits(5)) do
            local ok, ids = try(C_UnitAuras.GetUnitAuraInstanceIDs, unit, "HELPFUL", 20)
            if ok and type(ids) == "table" and #ids > 0 and not isSecret(ids[1]) then
                local m = revealTip.SetUnitBuffByAuraInstanceID or revealTip.SetUnitAuraByAuraInstanceID
                local okC, err = pcall(m, revealTip, unit, ids[1], "HELPFUL")
                outf("  Set(Buff)ByAuraInstanceID(%s) -> %s; line1=%s",
                    unit, okC and "ok" or ("ERR " .. tostring(err):sub(1, 40)), line1())
                break
            end
        end
    end
    if C_Timer then C_Timer.After(6, function() pcall(revealTip.Hide, revealTip) end) end
    emit("  line text is SECRET to us — VISUALLY confirm real names render on the tooltip (center of screen, 6s)")
end)

-------------------------------------------------------------------------------
--  T31 contextual ping oracle
-------------------------------------------------------------------------------
addTest("ping", "T31: Contextual ping-type identity oracle", function()
    if not (C_Ping and C_Ping.GetContextualPingTypeForUnit) then
        result("SKIP", "ping", "C_Ping.GetContextualPingTypeForUnit missing"); return
    end
    local units = mobUnits()
    if #units == 0 then result("SKIP", "ping", "no attackable mobs"); return end
    for _, unit in ipairs(units) do
        local guid = select(2, try(UnitGUID, unit)) -- may be secret; passed as-is
        local ok, pt = pcall(C_Ping.GetContextualPingTypeForUnit, guid)
        outf("  %-11s guid=%s -> pingType %s", unit, classify(guid),
            ok and classify(pt) or ("ERR " .. tostring(pt):sub(1, 40)))
    end
    emit("  plain PingSubjectType on a secret GUID = a classification oracle (attack/assist/threat)")
end)

-------------------------------------------------------------------------------
--  T32-T35 Group Finder taint battery, cast/death watch, timeline inject
-------------------------------------------------------------------------------
-- T32 hunts the carrier behind "execution tainted by 'EllesmereUIQoL'" raised
-- inside Blizzard's own LFGList paths (sort comparator, row initializer,
-- tooltip). issecurevariable([tbl,] "field") returns isSecure, taintingAddon —
-- the only API that NAMES the addon that wrote the value, so run this the
-- moment the error appears (taint is sticky until the next /reload).
addTest("lfg", "T32: Group Finder taint attribution (issecurevariable)", function()
    if not issecurevariable then
        result("FAIL", "issecurevariable", "API missing in this build"); return
    end

    -- tag/detail for one variable; tbl == nil probes a global by name.
    local function secureVar(tbl, field)
        local ok, isSecure, who
        if tbl == nil then
            ok, isSecure, who = pcall(issecurevariable, field)
        else
            ok, isSecure, who = pcall(issecurevariable, tbl, field)
        end
        if not ok then return "ERR", tostring(isSecure) end
        if isSecure then return "PASS", "secure" end
        return "FAIL", "INSECURE — tainted by " .. tostring(who or "<unknown>")
    end

    local function reportFields(label, tbl, fields)
        if type(tbl) ~= "table" then
            result("SKIP", label, "not loaded — open Group Finder and search first")
            return
        end
        for _, field in ipairs(fields) do
            local tag, detail = secureVar(tbl, field)
            result(tag, format("%s.%s", label, field), detail)
        end
    end

    -- 1) Context ---------------------------------------------------------
    local okL, lockdown = try(C_ChatInfo and C_ChatInfo.InChatMessagingLockdown)
    result(okL and "INFO" or "SKIP", "InChatMessagingLockdown()", classify(lockdown))

    local frame = LFGListFrame
    local panel = nil
    if type(frame) ~= "table" then
        result("SKIP", "LFGListFrame", "not loaded — open Group Finder and search first")
    else
        local _, shown = try(frame.IsShown, frame)
        panel = frame.SearchPanel
        outf("  LFGListFrame shown=%s  SearchPanel=%s", classify(shown),
            type(panel) == "table" and "present" or "missing")
    end

    -- #results is reused by the provider group below.
    local resultCount, results
    if type(panel) == "table" then
        outf("  SearchPanel.categoryID = %s", classify(panel.categoryID))
        local okR, r = pcall(function() return panel.results end)
        if okR then results = r end
        if results == nil then
            outf("  SearchPanel.results = nil — search first")
        elseif isSecret(results) then
            outf("  SearchPanel.results = SECRET (cannot index)")
        elseif type(results) ~= "table" then
            outf("  SearchPanel.results = %s", classify(results))
        else
            resultCount = #results
            outf("  SearchPanel.results count = %d", resultCount)
        end
    end

    -- 2) Field taint -----------------------------------------------------
    header("T32a: frame field taint")
    reportFields("LFGListFrame", frame,
        { "activePanel", "declines", "stopAssistPings", "displayedAutoAcceptConvert" })
    reportFields("SearchPanel", panel,
        { "results", "applications", "selectedResult", "totalResults",
          "searching", "searchFailed", "previousSearchText" })
    reportFields("LFGListApplicationDialog", LFGListApplicationDialog, { "resultID", "activityID" })

    -- 3) Global-function taint -------------------------------------------
    -- hooksecurefunc'd globals stay secure; INSECURE here means some addon
    -- replaced the global outright.
    header("T32b: global function taint")
    for _, name in ipairs({
        "LFGListApplicationDialog_Show", "LFGListSearchPanel_UpdateResultList",
        "LFGListSearchPanel_UpdateResults", "LFGListUtil_SortSearchResults",
        "LFGListUtil_SortSearchResultsCB", "LFGListSearchEntry_Update",
        "LFGListUtil_SetSearchEntryTooltip", "StaticPopupSpecial_Show",
        "StaticPopupSpecial_Hide",
    }) do
        if _G[name] == nil then
            result("SKIP", name, "global missing (Blizzard_GroupFinder not loaded?)")
        else
            local tag, detail = secureVar(nil, name)
            result(tag, name, detail)
        end
    end

    -- 4) Provider element taint (the tainted-row smoking gun) -------------
    header("T32c: ScrollBox dataProvider elements")
    local dataProvider
    if type(panel) == "table" then
        local okD, dp = pcall(function()
            local sb = panel.ScrollBox
            return sb and sb.GetDataProvider and sb:GetDataProvider()
        end)
        if not okD then
            result("ERR", "ScrollBox:GetDataProvider()", tostring(dp))
        else
            dataProvider = dp
        end
    end
    if type(dataProvider) ~= "table" then
        result("SKIP", "dataProvider", "none — open Group Finder and search first")
    else
        local okS, size = pcall(dataProvider.GetSize, dataProvider)
        outf("  provider size=%s  #results=%s",
            okS and tostring(size) or "ERR", tostring(resultCount))
        local last = (okS and type(size) == "number") and math.min(size, 5) or 0
        for i = 1, last do
            local okE, element = pcall(dataProvider.Find, dataProvider, i)
            if not okE then
                result("ERR", format("element[%d]", i), tostring(element))
            elseif type(element) ~= "table" then
                result("ERR", format("element[%d]", i), classify(element))
            else
                local tag, detail = secureVar(element, "resultID")
                local okV, v = pcall(function() return element.resultID end)
                result(tag, format("element[%d].resultID", i),
                    format("%s (value %s)", detail, okV and classify(v) or "ERR"))
            end
        end
    end

    -- 5) Sample result secrecy -------------------------------------------
    header("T32d: sample search-result secrecy")
    local first
    if type(results) == "table" and not isSecret(results) then first = results[1] end
    if first == nil then
        result("SKIP", "GetSearchResultInfo", "no results — open Group Finder and search first")
    elseif not (C_LFGList and C_LFGList.GetSearchResultInfo) then
        result("SKIP", "GetSearchResultInfo", "API missing")
    else
        local okI, info = pcall(C_LFGList.GetSearchResultInfo, first)
        if not okI then
            result("ERR", "GetSearchResultInfo", tostring(info))
        elseif info == nil or isSecret(info) then
            result("SECRET", "GetSearchResultInfo", classify(info))
        else
            local total, secretCount = 0, 0
            local okC = pcall(function()
                for _, v in pairs(info) do
                    total = total + 1
                    if isSecret(v) then secretCount = secretCount + 1 end
                end
            end)
            result(okC and "INFO" or "ERR", "GetSearchResultInfo",
                okC and format("%d/%d fields secret", secretCount, total) or "field walk failed")
            for _, field in ipairs({ "name", "activityIDs", "numMembers", "numBNetFriends", "isDelisted" }) do
                local okF, v = pcall(function() return info[field] end)
                outf("    .%-16s = %s", field, okF and classify(v) or "ERR")
            end
        end
    end

    -- 6) StaticPopupSpecial state ----------------------------------------
    -- Blizzard keeps the shown-dialog list as a file-local (shownDialogFrames
    -- in StaticPopup.lua), so there is no global backing table to walk; probe
    -- the LFG special dialogs' own StaticPopupSpecial bookkeeping instead.
    header("T32e: StaticPopupSpecial state")
    result("INFO", "shownDialogFrames", "file-local in StaticPopup.lua — not reachable from an addon")
    for _, name in ipairs({ "LFGListApplicationDialog", "LFGListInviteDialog" }) do
        local dialog = _G[name]
        if type(dialog) ~= "table" then
            result("SKIP", name, "not loaded — open Group Finder first")
        else
            local _, shown = try(dialog.IsShown, dialog)
            outf("  %s shown=%s", name, classify(shown))
            reportFields(name, dialog, { "special", "exclusive" })
        end
    end

    emit("  run this the moment a taint error fires — the INSECURE lines name the carrier")
end)

-------------------------------------------------------------------------------
-- T33 answers one question: does SecureActionButtonTemplate's click-forwarding
-- launder taint?
--
-- Every route to re-running a Group Finder search is closed to addons.
-- C_LFGList.Search carries HasRestrictions in the API export, and the only
-- other path, LFGListSearchPanel_DoSearch, reads self.previousSearchText at
-- LFGList.lua:2666 and writes it back at 2670 — so calling it (or :Click()ing
-- SearchPanel.RefreshButton, whose OnClick in LFGList.xml:1161 is exactly that
-- call) from addon code taints that field for the rest of the session. From
-- then on every search the PLAYER starts reads it and runs tainted, including
-- LFGListUtil_SortSearchResults and its numBNetFriends compare at 3973.
--
-- SECURE_ACTIONS.click (SecureTemplates.lua:554) is the one candidate escape:
-- Blizzard's own secure code performs delegate:Click(button) for us. The
-- pessimistic reading is that our SetAttribute("clickbutton", ...) taints the
-- attribute, SecureButton_GetModifiedAttribute reads it, and the forwarded
-- click is tainted anyway. The optimistic reading is that this is precisely
-- what the secure attribute system exists to survive — it is how every addon
-- action bar casts spells. This test settles it empirically instead.
--
-- Protocol (order matters — taint is sticky until /reload):
--   1. /reload, then open Group Finder → Dungeons and run one search.
--   2. /euidiag secureclick     — records a baseline and arms the probe button.
--      Every field must read PASS here. If any is already INSECURE the run is
--      contaminated; /reload and start over.
--   3. Click the on-screen "SECURE CLICK PROBE" button ONCE.
--   4. /euidiag secureclick     — the verdict.
--
-- Reading step 4: the witness is the DoSearch call count, NOT the search-event
-- count. calls>0 with every field still PASS means the forwarding laundered the
-- taint and an Apply Filter button is buildable. Any INSECURE field naming
-- EllesmereUISecretsDiag means it did not, and the route is dead. calls==0 is
-- INCONCLUSIVE: the click never reached DoSearch, so the fields are trivially
-- still secure and prove nothing.
--
-- The probe deliberately has NO OnClick or PostClick script of ours. Any
-- handler we attached would execute our own tainted code inside the same click
-- and manufacture the very result we are testing for.
-------------------------------------------------------------------------------
local SECURECLICK_FIELDS = {
    -- written directly by LFGListSearchPanel_DoSearch
    { "SearchPanel", "previousSearchText" },
    { "SearchPanel", "searching" },
    { "SearchPanel", "searchFailed" },
    { "SearchPanel", "selectedResult" },
    -- written by LFGListSearchPanel_UpdateResultList, which DoSearch calls
    { "SearchPanel", "results" },
    { "SearchPanel", "totalResults" },
    { "SearchPanel", "applications" },
    -- control: DoSearch never touches this, so it should stay PASS regardless
    { "LFGListFrame", "activePanel" },
}

local secureClickProbe        -- our SecureActionButtonTemplate button
local secureClickMode         -- "plain" (T33) or "preclick" (T34)
local secureClickWatcher      -- background search-event counter (see below)
local secureClickBaseline     -- field -> isSecure, captured at arm time
local secureClickSearches = 0
local secureClickArmedAt

-- LFG_LIST_SEARCH_RESULTS_RECEIVED fires on server-pushed updates to a search
-- that is ALREADY running, not only on new ones — so counting it cannot tell a
-- successful forwarded click apart from an idle panel being refreshed by the
-- server. The only trustworthy witness that DoSearch actually executed is a
-- post-hook on DoSearch itself. hooksecurefunc post-hooks run after the
-- original returns and do not taint the secure caller, and this one only reads,
-- so it cannot manufacture the result it is measuring.
local secureClickDoSearch = {}   -- { { t, secure, who } } one entry per real call
local secureClickHooked = false

-- Did the click even reach our button? PostClick is safe to instrument: the
-- DoSearch hook above has already taken its reading by the time this runs, and
-- this handler touches nothing but a local counter — it cannot reach back and
-- taint a write that has already happened.
local secureClickPostClicks = 0
local secureClickPreClicks = 0
-- Timestamps of every click edge, so each DoSearch call can be attributed to the
-- probe or to something else. Navigating to the search panel calls DoSearch on
-- its own (LFGList.lua:502/722/1994/2604), which otherwise inflates the witness
-- count and makes an unattributed run look like a pass.
local secureClickTimes = {}
local SECURECLICK_WINDOW = 0.5   -- seconds; a forwarded DoSearch is synchronous

local function secureClickInstallHook()
    if secureClickHooked then return true end
    if type(LFGListSearchPanel_DoSearch) ~= "function" then return false end
    hooksecurefunc("LFGListSearchPanel_DoSearch", function(panel)
        -- DoSearch writes previousSearchText as its last statement (2670), so
        -- this is the freshest possible reading of whether that write was
        -- secure.
        local ok, isSecure, who = pcall(issecurevariable, panel, "previousSearchText")
        -- `ok and isSecure or nil` would collapse an INSECURE (false) reading
        -- into nil/"unreadable"; keep the three states distinct.
        local entry = { t = GetTime(), who = who }
        if ok then entry.secure = (isSecure == true) end
        secureClickDoSearch[#secureClickDoSearch + 1] = entry
    end)
    secureClickHooked = true
    return true
end

local function secureClickTargets()
    local frame = LFGListFrame
    if type(frame) ~= "table" then return nil end
    local panel = frame.SearchPanel
    if type(panel) ~= "table" then return nil end
    return frame, panel
end

local function secureClickHolder(name, frame, panel)
    if name == "SearchPanel" then return panel end
    return frame
end

-- Everything about the probe button that could explain a click going nowhere.
local function secureClickDumpProbe(panel)
    local b = secureClickProbe
    if not b then result("SKIP", "probe state", "not built"); return end

    local _, shown = try(b.IsShown, b)
    local _, visible = try(b.IsVisible, b)
    local _, mouse = try(b.IsMouseEnabled, b)
    local _, strata = try(b.GetFrameStrata, b)
    local _, level = try(b.GetFrameLevel, b)
    local _, alpha = try(b.GetEffectiveAlpha, b)
    outf("  probe: shown=%s visible=%s mouseEnabled=%s strata=%s level=%s alpha=%s",
        tostring(shown), tostring(visible), tostring(mouse),
        tostring(strata), tostring(level), tostring(alpha))

    local _, left = try(b.GetLeft, b)
    local _, bottom = try(b.GetBottom, b)
    local _, w = try(b.GetWidth, b)
    local _, h = try(b.GetHeight, b)
    outf("  probe rect: left=%s bottom=%s w=%s h=%s (screen %sx%s)",
        tostring(left and floor(left)), tostring(bottom and floor(bottom)),
        tostring(w and floor(w)), tostring(h and floor(h)),
        tostring(floor(GetScreenWidth() or 0)), tostring(floor(GetScreenHeight() or 0)))

    local _, atype = try(b.GetAttribute, b, "type")
    local _, delegate = try(b.GetAttribute, b, "clickbutton")
    outf("  probe attrs: type=%s clickbutton=%s matchesRefreshButton=%s",
        tostring(atype), tostring(delegate),
        tostring(delegate ~= nil and delegate == panel.RefreshButton))

    local rb = panel.RefreshButton
    if type(rb) == "table" then
        local _, rbShown = try(rb.IsVisible, rb)
        local _, rbEnabled = try(rb.IsEnabled, rb)
        local _, forbidden = try(rb.IsForbidden, rb)
        outf("  RefreshButton: visible=%s enabled=%s forbidden=%s",
            tostring(rbShown), tostring(rbEnabled), tostring(forbidden))
    end

    -- These two decide which click edge SecureActionButton_OnClick acts on when
    -- the caller is an addon; see the RegisterForClicks comment.
    outf("  CVars: ActionButtonUseKeyDown=%s ActionButtonUseKeyHeldSpell=%s",
        tostring(GetCVarBool("ActionButtonUseKeyDown")),
        tostring(GetCVarBool("ActionButtonUseKeyHeldSpell")))
    outf("  mode=%s  PostClick fires: %d  PreClick fires: %d",
        tostring(secureClickMode), secureClickPostClicks, secureClickPreClicks)
end

-- issecurevariable(tbl, field) -> isSecure, taintingAddon. Returns tag, detail,
-- isSecure so callers can both print and compare against the baseline.
local function secureClickVar(tbl, field)
    local ok, isSecure, who = pcall(issecurevariable, tbl, field)
    if not ok then return "ERR", tostring(isSecure), nil end
    if isSecure then return "PASS", "secure", true end
    return "FAIL", "INSECURE — tainted by " .. tostring(who or "<unknown>"), false
end

-- Stand-in for what a real Premade Filter option toggle does on click: mutate
-- our own saved state, then push it to Blizzard's C-side advanced filter. The
-- SaveAdvancedFilter call is a round-trip of the live filter, so it changes
-- nothing — the point is to execute representative addon code, not to alter the
-- search.
local secureClickFakeDB = {}

local function secureClickPreClickWork()
    secureClickPreClicks = secureClickPreClicks + 1
    secureClickTimes[#secureClickTimes + 1] = GetTime()
    secureClickFakeDB.lastToggle = GetTime()
    secureClickFakeDB.dungeons = secureClickFakeDB.dungeons or {}
    secureClickFakeDB.dungeons[#secureClickFakeDB.dungeons + 1] = secureClickPreClicks
    if C_LFGList and C_LFGList.GetAdvancedFilter and C_LFGList.SaveAdvancedFilter then
        pcall(function()
            C_LFGList.SaveAdvancedFilter(C_LFGList.GetAdvancedFilter())
        end)
    end
end

local function secureClickBuild(panel, mode)
    if secureClickProbe then return true end
    if InCombatLockdown() then return false, "in combat — secure attributes are locked" end

    local b = CreateFrame("Button", "EUISecretsDiagSecureClickProbe", UIParent,
        "SecureActionButtonTemplate")
    b:SetSize(210, 34)
    -- Top of the screen at TOOLTIP strata: PVEFrame is centred and sits at
    -- DIALOG, so the original CENTER/DIALOG placement could be swallowed by the
    -- very frame this test requires you to have open.
    b:SetPoint("TOP", UIParent, "TOP", 0, -120)
    b:SetFrameStrata("TOOLTIP")
    b:SetFrameLevel(500)
    -- Both edges, deliberately. SecureTemplates.xml:6 says addons cannot supply
    -- SecureActionButton_OnClick's isKeyPress/isSecureAction arguments, so they
    -- arrive nil, isSecureMousePress is falsy, and useOnKeyDown collapses to the
    -- ActionButtonUseKeyDown CVar. clickAction is then
    --   (down and useOnKeyDown) or (not down and not useOnKeyDown)
    -- which fires on exactly one of the two edges whichever way the CVar is set,
    -- and on NEITHER if we only register the wrong one. Registering both makes
    -- the test CVar-independent and still yields exactly one action per click.
    b:RegisterForClicks("AnyUp", "AnyDown")

    local bg = b:CreateTexture(nil, "BACKGROUND")
    bg:SetAllPoints(b)
    bg:SetColorTexture(0.05, 0.5, 0.4, 0.85)
    local label = b:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    label:SetPoint("CENTER")
    label:SetText(mode == "preclick" and "PRECLICK PROBE" or "SECURE CLICK PROBE")
    b:SetHighlightTexture("Interface\\Buttons\\UI-Common-MouseHilight", "ADD")
    -- Pure-texture press feedback; a script here would run our tainted code
    -- inside the click and contaminate the measurement.
    b:SetPushedTexture("Interface\\Buttons\\UI-Quickslot-Depress")

    -- The whole experiment: Blizzard's SECURE_ACTIONS.click performs the
    -- delegate click, so the RefreshButton OnClick may run outside our taint.
    b:SetAttribute("type", "click")
    b:SetAttribute("clickbutton", panel.RefreshButton)

    b:SetScript("PostClick", function()
        secureClickPostClicks = secureClickPostClicks + 1
        secureClickTimes[#secureClickTimes + 1] = GetTime()
    end)

    -- T34 only. PreClick runs BEFORE SecureActionButton_OnClick, which is the
    -- only slot where a real option toggle could save its setting in time for
    -- the same click's search to see it (PostClick is too late — the search
    -- would apply the previous state). Whether our code here bleeds taint into
    -- the forwarded click is exactly what T34 measures.
    if mode == "preclick" then
        b:SetScript("PreClick", secureClickPreClickWork)
    end

    secureClickMode = mode
    secureClickProbe = b

    secureClickWatcher = CreateFrame("Frame")
    secureClickWatcher:RegisterEvent("LFG_LIST_SEARCH_RESULTS_RECEIVED")
    secureClickWatcher:RegisterEvent("LFG_LIST_SEARCH_FAILED")
    secureClickWatcher:SetScript("OnEvent", function()
        secureClickSearches = secureClickSearches + 1
    end)

    return true
end

local function secureClickRun(mode, tag)
    if not issecurevariable then
        result("FAIL", "issecurevariable", "API missing in this build"); return
    end

    local frame, panel = secureClickTargets()
    if not panel then
        result("SKIP", "LFGListFrame.SearchPanel",
            "not loaded — open Group Finder → Dungeons and search first")
        return
    end
    if type(panel.RefreshButton) ~= "table" then
        result("SKIP", "SearchPanel.RefreshButton", "missing — Blizzard_GroupFinder layout changed?")
        return
    end

    -- One probe per session: the two tests differ only by a PreClick script, and
    -- taint is sticky, so a T33 run followed by a T34 run in the same session
    -- would report T33's state.
    if secureClickProbe and secureClickMode ~= mode then
        result("SKIP", format("%s probe", tag), format(
            "a '%s' probe is already armed this session — /reload before running this one",
            tostring(secureClickMode)))
        return
    end

    local armed = secureClickProbe ~= nil

    -- Read the fields first, before anything else this test does can move them.
    header(armed and (tag .. "b: verdict") or (tag .. "a: baseline"))
    local snapshot, regressions = {}, 0
    for _, pair in ipairs(SECURECLICK_FIELDS) do
        local holderName, field = pair[1], pair[2]
        local holder = secureClickHolder(holderName, frame, panel)
        local tag, detail, isSecure = secureClickVar(holder, field)
        snapshot[holderName .. "." .. field] = isSecure
        local label = format("%s.%s", holderName, field)
        if armed and secureClickBaseline then
            local before = secureClickBaseline[holderName .. "." .. field]
            if before == true and isSecure == false then
                regressions = regressions + 1
                detail = detail .. "  <<< WAS SECURE BEFORE THE CLICK"
            end
        end
        result(tag, label, detail)
    end

    if not armed then
        local ok, why = secureClickBuild(panel, mode)
        if not ok then
            result("SKIP", "probe button", why)
            return
        end
        if not secureClickInstallHook() then
            result("SKIP", "DoSearch hook",
                "LFGListSearchPanel_DoSearch missing — cannot witness the click")
            return
        end
        secureClickBaseline = snapshot
        secureClickSearches = 0
        secureClickPostClicks = 0
        secureClickPreClicks = 0
        wipe(secureClickTimes)
        wipe(secureClickDoSearch)
        secureClickArmedAt = GetTime()
        secureClickDumpProbe(panel)

        -- Arming with the search panel hidden means navigating to it afterwards
        -- fires DoSearch on its own, which muddies attribution.
        local _, rbVisible = try(panel.RefreshButton.IsVisible, panel.RefreshButton)
        if not rbVisible then
            result("FAIL", "panel visibility", "the Dungeons search panel is NOT open — " ..
                "open it and run a search BEFORE arming, or navigating to it will fire " ..
                "DoSearch by itself and pollute the witness count")
        end

        local dirty = 0
        for _, v in pairs(snapshot) do if v == false then dirty = dirty + 1 end end
        if dirty > 0 then
            result("FAIL", "baseline", format(
                "%d field(s) already INSECURE — this run is contaminated, /reload and redo", dirty))
        else
            result("PASS", "baseline", "all fields secure — clean starting point")
        end

        outf("  armed (%s). Click the green probe button once (top centre of screen), then", mode)
        outf("  run /euidiag %s again. Do not search by hand in between —",
            mode == "preclick" and "preclick" or "secureclick")
        emit("  a manual search also calls DoSearch and muddies the witness count.")
        emit("  Do NOT /reload in between — that would wipe the evidence.")
        return
    end

    -- Verdict --------------------------------------------------------------
    -- DoSearch calls are the witness. Search EVENTS are reported only to show
    -- the confound: the server pushes those to an idle panel on its own.
    local calls = #secureClickDoSearch
    outf("  DoSearch calls since arming: %d   (background search events: %d, %.1fs elapsed)",
        calls, secureClickSearches, GetTime() - (secureClickArmedAt or GetTime()))
    secureClickDumpProbe(panel)

    -- Attribute each call. Only a call that lands inside a click edge's window
    -- can be OUR forwarded search; the rest are Blizzard's own (opening or
    -- navigating to the search panel calls DoSearch by itself).
    local attributed = 0
    for i = 1, calls do
        local c = secureClickDoSearch[i]
        local best
        for j = 1, #secureClickTimes do
            local d = c.t - secureClickTimes[j]
            if d < 0 then d = -d end
            if not best or d < best then best = d end
        end
        local mine = best ~= nil and best <= SECURECLICK_WINDOW
        if mine then attributed = attributed + 1 end
        outf("    call %d at +%.1fs [%s] — previousSearchText immediately after: %s", i,
            c.t - (secureClickArmedAt or c.t),
            mine and format("PROBE, %.2fs from a click edge", best)
                or (best and format("not ours, nearest click %.1fs away", best) or "not ours"),
            c.secure == true and "secure"
                or c.secure == false and ("INSECURE, tainted by " .. tostring(c.who or "<unknown>"))
                or "unreadable")
    end
    outf("  DoSearch calls attributable to the probe: %d of %d", attributed, calls)

    if calls > 0 and attributed == 0 then
        result("FAIL", "verdict", format(
            "UNATTRIBUTED — DoSearch ran %d time(s) but none within %.1fs of a click edge, " ..
            "so every one of them was Blizzard's own (opening/navigating the search panel). " ..
            "The forwarded click did nothing and the all-secure sweep proves nothing. " ..
            "Re-run with the Dungeons search panel already open and visible before arming.",
            calls, SECURECLICK_WINDOW))
    elseif calls == 0 and secureClickPostClicks == 0 then
        result("INFO", "verdict", "INCONCLUSIVE — the probe button was never clicked " ..
            "(PostClick fired 0 times), so nothing was tested. All-secure above is the null " ..
            "result. Click the green button itself, not anything in the Group Finder, then re-run.")
    elseif calls == 0 then
        result("FAIL", "verdict", format(
            "BLOCKED — the button WAS clicked (%d PostClick fires; both edges are " ..
            "registered, so one physical click counts twice) but DoSearch never ran. " ..
            "SECURE_ACTIONS.click refused or dropped the delegate — check the attrs above.",
            secureClickPostClicks))
    elseif mode == "preclick" and secureClickPreClicks == 0 then
        result("INFO", "verdict", "INCONCLUSIVE — DoSearch ran but the PreClick handler " ..
            "never fired, so nothing was tested. The script did not attach.")
    elseif regressions > 0 then
        if mode == "preclick" then
            result("FAIL", "verdict", format(
                "PRECLICK CONTAMINATES — %d field(s) went secure -> INSECURE. Our code " ..
                "running in PreClick bleeds taint into the forwarded click, so an option " ..
                "toggle cannot save-and-search on one click. A separate Apply button " ..
                "(no PreClick) remains the answer.", regressions))
        else
            result("FAIL", "verdict", format(
                "DEAD END — %d field(s) went secure -> INSECURE across the forwarded click. " ..
                "SecureActionButtonTemplate does NOT launder taint here; an Apply Filter " ..
                "button is not buildable.", regressions))
        end
    elseif mode == "preclick" then
        result("PASS", "verdict", format(
            "PRECLICK IS CLEAN — %d PreClick fire(s), %d of %d DoSearch call(s) traced to the " ..
            "probe, every field still secure. Option toggles CAN save their setting and " ..
            "re-run the search on the same click; no separate Apply button needed.",
            secureClickPreClicks, attributed, calls))
    else
        result("PASS", "verdict", format(
            "LAUNDERED — %d of %d DoSearch call(s) traced to the probe and every field is " ..
            "still secure. Click-forwarding survives; an Apply Filter button IS buildable.",
            attributed, calls))
    end
    emit("  /reload when finished to remove the probe button.")
end

addTest("secureclick", "T33: SecureActionButton click-forwarding taint laundering", function()
    secureClickRun("plain", "T33")
end, true)

-------------------------------------------------------------------------------
-- T34 is T33 plus a PreClick handler doing representative option-toggle work
-- (mutate our own table, then C_LFGList.SaveAdvancedFilter). PreClick is the
-- only slot where a filter toggle could save its setting in time for the same
-- click's search to pick it up — PostClick runs after the secure action, so the
-- search would apply the previous state and every toggle would lag by one.
--
-- If this passes, the Premade Filter panel's own checkboxes can BE the secure
-- forwarders and the search re-runs the instant you change a dungeon or role.
-- If it fails, PreClick bleeds taint and a separate Apply button is the ceiling.
-------------------------------------------------------------------------------
addTest("preclick", "T34: does a PreClick handler contaminate the forwarded click?", function()
    secureClickRun("preclick", "T34")
end, true)

-------------------------------------------------------------------------------
-- T35: raid true-filtering via C_LFGList.SetSearchToActivity + secure refresh
--
-- DoSearch nils the advanced filter off-dungeon (LFGList.lua:2653), but the
-- engine-side "search text fields" are a second channel it never touches:
-- C_LFGList.SetSearchToActivity primes them (the search-box autocomplete path,
-- LFGList.lua:2867/2922) and the next Search — whoever runs it — returns only
-- that activity's rows, on ANY category. RefreshButton's OnClick is nothing
-- but PlaySound + DoSearch (LFGList.xml), so the primed state should survive a
-- secure-forwarded refresh and give true row removal on the raid browse.
--
-- The prime is deliberately done at ARM time, not in a PreClick: the longer
-- the gap between the prime and the click, the stronger the evidence that the
-- engine state survives idle UI time. If this passes, the production panel can
-- prime in PreClick (a strictly shorter window, and T34 already cleared
-- PreClick work).
--
-- Protocol (taint is sticky — /reload first if anything reads INSECURE):
--   1. /reload, open Group Finder -> Raids (or Legacy Raids), run one search.
--   2. /euidiag prime list           — pick an activityID.
--   3. /euidiag prime <activityID>   — baseline + arm the probe + prime.
--   4. Click the orange PRIME REFRESH PROBE button ONCE; wait for the list.
--   5. /euidiag prime                — the verdict.
--   6. /euidiag prime clear, click the probe again, then /euidiag prime — the
--      list must return to the full set with every field still secure.
--
-- All T35 state lives in one table: this file is close enough to the main
-- chunk's 200-local ceiling that another spread of top-level locals is a
-- build risk.
-------------------------------------------------------------------------------
local Prime = { doSearch = {}, clickTimes = {}, snapshots = {}, postClicks = 0 }

-- Census of the current search results: how many rows match the primed
-- activity, how many don't, how many are unreadable (secret). Runs inside the
-- results event, independent of the panel's own rendering.
function Prime.scanResults()
    local ok0, _, ids = pcall(function() return C_LFGList.GetSearchResults() end)
    if not ok0 or type(ids) ~= "table" then return end
    local snap = { t = GetTime(), n = #ids, match = 0, other = 0, secret = 0,
                   activityID = Prime.activityID, others = {} }
    for i = 1, math.min(#ids, 100) do
        local matched, otherID
        -- One pcall per row: a secret info table, a secret activityIDs table
        -- or a secret element all throw somewhere in here, and any throw means
        -- "unreadable", never "mismatch".
        local ok = pcall(function()
            local info = C_LFGList.GetSearchResultInfo(ids[i])
            if isSecret(info) then error("secret", 0) end
            local acts = info and info.activityIDs
            if isSecret(acts) then error("secret", 0) end
            for _, a in ipairs(acts) do
                if isSecret(a) then error("secret", 0) end
                if a == Prime.activityID then matched = true else otherID = a end
            end
        end)
        if not ok then
            snap.secret = snap.secret + 1
        elseif matched then
            snap.match = snap.match + 1
        else
            snap.other = snap.other + 1
            if otherID and #snap.others < 3 then snap.others[#snap.others + 1] = otherID end
        end
    end
    Prime.snapshots[#Prime.snapshots + 1] = snap
end

function Prime.fieldSweep(frame, panel, compare)
    local snapshot, regressions, regressed = {}, 0, {}
    for _, pair in ipairs(SECURECLICK_FIELDS) do
        local holderName, field = pair[1], pair[2]
        local tag, detail, isSecure =
            secureClickVar(secureClickHolder(holderName, frame, panel), field)
        snapshot[holderName .. "." .. field] = isSecure
        if compare and compare[holderName .. "." .. field] == true and isSecure == false then
            regressions = regressions + 1
            regressed[#regressed + 1] = holderName .. "." .. field
            detail = detail .. "  <<< WAS SECURE BEFORE THE CLICK"
        end
        result(tag, format("%s.%s", holderName, field), detail)
    end
    return snapshot, regressions, regressed
end

function Prime.build(panel)
    if Prime.probe then return true end
    if InCombatLockdown() then return false, "in combat — secure attributes are locked" end

    local b = CreateFrame("Button", "EUISecretsDiagPrimeProbe", UIParent,
        "SecureActionButtonTemplate")
    b:SetSize(210, 34)
    -- Below the T33/T34 probe slot so both can exist in one session.
    b:SetPoint("TOP", UIParent, "TOP", 0, -160)
    b:SetFrameStrata("TOOLTIP")
    b:SetFrameLevel(500)
    -- Both edges, CVar-independent, one action per click — see the T33 comment.
    b:RegisterForClicks("AnyUp", "AnyDown")

    local bg = b:CreateTexture(nil, "BACKGROUND")
    bg:SetAllPoints(b)
    bg:SetColorTexture(0.75, 0.4, 0.05, 0.85)
    local label = b:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    label:SetPoint("CENTER")
    label:SetText("PRIME REFRESH PROBE")
    b:SetHighlightTexture("Interface\\Buttons\\UI-Common-MouseHilight", "ADD")
    b:SetPushedTexture("Interface\\Buttons\\UI-Quickslot-Depress")

    b:SetAttribute("type", "click")
    b:SetAttribute("clickbutton", panel.RefreshButton)

    -- No PreClick, on purpose: the prime already happened at arm time, and a
    -- handler here would run tainted code inside the very click under test.
    b:SetScript("PostClick", function()
        Prime.postClicks = Prime.postClicks + 1
        Prime.clickTimes[#Prime.clickTimes + 1] = GetTime()
    end)
    Prime.probe = b

    local w = CreateFrame("Frame")
    w:RegisterEvent("LFG_LIST_SEARCH_RESULTS_RECEIVED")
    w:RegisterEvent("LFG_LIST_SEARCH_FAILED")
    w:SetScript("OnEvent", function(_, event)
        if event == "LFG_LIST_SEARCH_FAILED" then
            Prime.snapshots[#Prime.snapshots + 1] = { t = GetTime(), failed = true }
        else
            Prime.scanResults()
        end
    end)
    Prime.watcher = w
    return true
end

function Prime.installHook()
    if Prime.hooked then return true end
    if type(LFGListSearchPanel_DoSearch) ~= "function" then return false end
    -- Same witness as T33: previousSearchText is DoSearch's last write, so its
    -- security immediately after is the freshest reading of the whole call.
    hooksecurefunc("LFGListSearchPanel_DoSearch", function(panel)
        local ok, isSecure, who = pcall(issecurevariable, panel, "previousSearchText")
        -- Keep secure=false distinct from "pcall failed" (see the T33 hook).
        local entry = { t = GetTime(), who = who }
        if ok then entry.secure = (isSecure == true) end
        Prime.doSearch[#Prime.doSearch + 1] = entry
    end)
    Prime.hooked = true
    return true
end

function Prime.list(panel)
    header("T35: available activities on the open browse category")
    local catID = panel.categoryID
    if type(catID) ~= "number" then
        result("SKIP", "panel.categoryID", "no category selected — open a browse panel first")
        return
    end
    local okC, catInfo = try(C_LFGList.GetLfgCategoryInfo, catID)
    outf("  categoryID=%d (%s)  panel.filters=%s  preferredFilters=%s", catID,
        (okC and type(catInfo) == "table") and tostring(catInfo.name) or "?",
        tostring(panel.filters), tostring(panel.preferredFilters))

    -- ResolveCategoryFilters is a Blizzard local, but it is the identity for
    -- every category except Dungeons, so panel.filters is the exact mirror of
    -- what DoSearch passes for the raid browse.
    local ok, acts = try(C_LFGList.GetAvailableActivities, catID, nil, panel.filters or 0)
    if not ok or type(acts) ~= "table" then
        result("ERR", "GetAvailableActivities", tostring(acts))
        return
    end
    outf("  %d activities:", #acts)
    for i = 1, math.min(#acts, 60) do
        local id = acts[i]
        local okI, info = try(C_LFGList.GetActivityInfoTable, id)
        local name, marks = "?", {}
        if okI and type(info) == "table" then
            name = tostring(info.fullName)
            if info.isCurrentRaidActivity then marks[#marks + 1] = "current-raid" end
            if info.difficultyID and info.difficultyID ~= 0 then
                marks[#marks + 1] = "diff=" .. tostring(info.difficultyID)
            end
        end
        outf("    %6d — %s%s", id, name,
            #marks > 0 and ("  [" .. table.concat(marks, " ") .. "]") or "")
    end
    if #acts > 60 then outf("    (... %d more truncated)", #acts - 60) end
    emit("  /euidiag prime <activityID> to arm")
end

function Prime.arm(frame, panel, id)
    header("T35a: baseline + arm + prime")
    local okI, info = try(C_LFGList.GetActivityInfoTable, id)
    if not okI or type(info) ~= "table" then
        result("FAIL", "activityID " .. id,
            "GetActivityInfoTable returned nothing — pick one from /euidiag prime list")
        return
    end
    outf("  activity %d = %s (categoryID=%s)", id, tostring(info.fullName),
        tostring(info.categoryID))
    if info.categoryID ~= panel.categoryID then
        result("FAIL", "category mismatch", format(
            "activity belongs to category %s but the open panel is %s — cross-category " ..
            "prime behaviour is untested; open the matching browse first",
            tostring(info.categoryID), tostring(panel.categoryID)))
        return
    end

    local ok, why = Prime.build(panel)
    if not ok then result("SKIP", "probe button", why); return end
    if not Prime.installHook() then
        result("SKIP", "DoSearch hook",
            "LFGListSearchPanel_DoSearch missing — cannot witness the click")
        return
    end

    local snapshot = Prime.fieldSweep(frame, panel, nil)
    local dirty = 0
    for _, v in pairs(snapshot) do if v == false then dirty = dirty + 1 end end
    if dirty > 0 then
        result("FAIL", "baseline", format(
            "%d field(s) already INSECURE — this run is contaminated, /reload and redo", dirty))
    else
        result("PASS", "baseline", "all fields secure — clean starting point")
    end

    local _, rbVisible = try(panel.RefreshButton.IsVisible, panel.RefreshButton)
    if not rbVisible then
        result("FAIL", "panel visibility", "the search panel is NOT open — open the raid " ..
            "browse and search BEFORE arming, or navigation will fire DoSearch by itself " ..
            "and pollute the witness count")
    end

    Prime.baseline = snapshot
    Prime.armedAt = GetTime()
    Prime.activityID = id
    Prime.cleared = nil
    Prime.postClicks = 0
    wipe(Prime.clickTimes)
    wipe(Prime.doSearch)
    wipe(Prime.snapshots)

    local okP, err = try(C_LFGList.SetSearchToActivity, id)
    if not okP then
        result("ERR", "SetSearchToActivity", tostring(err))
        return
    end
    result("PASS", "SetSearchToActivity", format("primed to %d (%s)", id, tostring(info.fullName)))
    emit("  now click the orange PRIME REFRESH PROBE button ONCE, wait for the list to")
    emit("  repopulate, then run /euidiag prime for the verdict. Do not search by hand or")
    emit("  touch the search box in between; do NOT /reload — that wipes the evidence.")
end

function Prime.verdict(frame, panel)
    header("T35b: verdict")
    local _, regressions, regressed = Prime.fieldSweep(frame, panel, Prime.baseline)

    -- Known value-taint, NOT benign (second live run 2026-08-02): the prime
    -- writes the activity name into the secure search box under our
    -- attribution, and DoSearch's FIRST line reads it back (LFGList.lua:2650).
    -- Comparing a tainted value does not propagate (the 2666 compare is clean),
    -- but READING one does: the whole primed DoSearch runs tainted from 2650,
    -- including the inline UpdateResultList at 2662 — a full tainted Blizzard
    -- rebuild per primed search. The RESULTS_RECEIVED event rebuild launders
    -- the visible fields moments later (which is why this sweep looks clean),
    -- but interacting with the primed list (row click -> sign-up) seeds the
    -- session-sticky trio: LFGListFrame.activePanel and
    -- LFGListApplicationDialog.resultID/.activityID — confirmed by /euidiag
    -- lfg after a sign-up from a primed raid list. In lockdown the tainted
    -- inline rebuild also throws on secret fields (the 3187 class). This is
    -- why the filter panel does NOT ship the prime. The expected flag below
    -- only keeps the single-field regression from masking OTHER contamination.
    local benign = regressions == 1 and regressed[1] == "SearchPanel.previousSearchText"
        and not Prime.cleared
    if benign then
        result("INFO", "previousSearchText regression", "expected from the primed box " ..
            "text — but NOT harmless: the primed DoSearch runs tainted from " ..
            "GetText (2650) through its inline rebuild (2662). Do not sign up " ..
            "from a primed list; run /euidiag lfg after any interaction.")
    end

    local calls = #Prime.doSearch
    outf("  DoSearch calls since arming: %d   (probe PostClick fires: %d, %.1fs elapsed)",
        calls, Prime.postClicks, GetTime() - (Prime.armedAt or GetTime()))

    local attributed = 0
    for i = 1, calls do
        local c = Prime.doSearch[i]
        local best
        for j = 1, #Prime.clickTimes do
            local d = c.t - Prime.clickTimes[j]
            if d < 0 then d = -d end
            if not best or d < best then best = d end
        end
        local mine = best ~= nil and best <= SECURECLICK_WINDOW
        if mine then attributed = attributed + 1 end
        outf("    call %d at +%.1fs [%s] — previousSearchText immediately after: %s", i,
            c.t - (Prime.armedAt or c.t),
            mine and format("PROBE, %.2fs from a click edge", best)
                or (best and format("not ours, nearest click %.1fs away", best) or "not ours"),
            c.secure == true and "secure"
                or c.secure == false and ("INSECURE, tainted by " .. tostring(c.who or "<unknown>"))
                or "unreadable")
    end

    local snap
    for i = #Prime.snapshots, 1, -1 do
        if not Prime.snapshots[i].failed then snap = Prime.snapshots[i]; break end
    end
    if snap then
        outf("  latest result census: %d rows — %d match activity %s, %d other, %d secret/unreadable%s",
            snap.n, snap.match, tostring(snap.activityID), snap.other, snap.secret,
            #snap.others > 0 and ("  (sample off-activity IDs: " ..
                table.concat(snap.others, ", ") .. ")") or "")
    end

    -- Taint first: a filtering win on a poisoned session is still a dead end.
    if regressions > 0 and not benign then
        result("FAIL", "verdict", format(
            "TAINTED — %d field(s) went secure -> INSECURE across the primed refresh " ..
            "(beyond the known previousSearchText value-taint). The prime route is not " ..
            "clean; do not build on it.", regressions))
    elseif calls == 0 and Prime.postClicks == 0 then
        result("INFO", "verdict", "INCONCLUSIVE — the probe button was never clicked " ..
            "(PostClick fired 0 times). Click the orange button itself, then re-run.")
    elseif calls == 0 then
        result("FAIL", "verdict", format(
            "BLOCKED — the button WAS clicked (%d PostClick fires; both edges are " ..
            "registered, so one physical click counts twice) but DoSearch never ran. " ..
            "SECURE_ACTIONS.click refused or dropped the delegate.", Prime.postClicks))
    elseif attributed == 0 then
        result("FAIL", "verdict", format(
            "UNATTRIBUTED — DoSearch ran %d time(s) but none within %.1fs of a click " ..
            "edge, so every one was Blizzard's own. Re-run with the raid browse already " ..
            "open and visible before arming.", calls, SECURECLICK_WINDOW))
    elseif not snap then
        result("INFO", "verdict", "NO RESULTS EVENT yet — the search may still be in " ..
            "flight (or every attempt hit LFG_LIST_SEARCH_FAILED). Wait for the list and re-run.")
    elseif Prime.cleared then
        result(regressions == 0 and "PASS" or "FAIL", "verdict (post-clear)", format(
            "fields secure after ClearSearchTextFields + refresh; list shows %d rows " ..
            "(%d unreadable). Confirm visually that the full set is back.",
            snap.n, snap.secret))
    elseif snap.other > 0 then
        result("FAIL", "verdict", format(
            "PRIME NOT HONOURED — %d of %d readable rows are a different activity. The " ..
            "primed state was ignored, clobbered before the click, or does not filter " ..
            "server-side on this category.", snap.other, snap.match + snap.other))
    elseif snap.match > 0 then
        result("PASS", "verdict", format(
            "TRUE FILTERING — all %d readable rows match the primed activity, %d DoSearch " ..
            "call(s) traced to the probe, every swept field still secure. CAUTION: this " ..
            "sweep runs after the event rebuild has laundered the visible fields; the " ..
            "primed search's INLINE rebuild still ran tainted (see the banner). Row " ..
            "removal works; the route is still not shippable.",
            snap.match, attributed))
        emit("  next: /euidiag prime clear, click the probe once more, and re-run to")
        emit("  confirm the un-filter path. Do NOT sign up from a primed list.")
    elseif snap.n == 0 then
        result("INFO", "verdict", "ZERO RESULTS — clean taint-wise, but an empty list " ..
            "can't distinguish 'filtered perfectly' from 'nobody is listing this " ..
            "activity'. Re-arm with a busier activity (current raid, Normal/Heroic).")
    else
        result("INFO", "verdict", format(
            "UNREADABLE — every row (%d) is secret, so the census can't confirm the " ..
            "filter. Taint is clean; verify visually that only the primed activity shows.",
            snap.secret))
    end
    emit("  /reload when finished to remove the probe button.")
end

function Prime.clear()
    header("T35c: clear")
    local ok, err = try(C_LFGList.ClearSearchTextFields)
    if not ok then
        result("ERR", "ClearSearchTextFields", tostring(err))
        return
    end
    Prime.cleared = true
    Prime.activityID = nil
    result("PASS", "ClearSearchTextFields", "engine search fields cleared")
    emit("  click the probe once more; the list should return to the full set. Then run")
    emit("  /euidiag prime — fields must still be secure and the row count should jump.")
end

function Prime.run(arg)
    if not issecurevariable then
        result("FAIL", "issecurevariable", "API missing in this build")
        return
    end
    local frame, panel = secureClickTargets()
    if not panel then
        result("SKIP", "LFGListFrame.SearchPanel",
            "not loaded — open Group Finder -> Raids and search first")
        return
    end
    if type(panel.RefreshButton) ~= "table" then
        result("SKIP", "SearchPanel.RefreshButton", "missing — Blizzard_GroupFinder layout changed?")
        return
    end

    local id = tonumber(arg)
    if arg == "list" then
        Prime.list(panel)
    elseif arg == "clear" then
        Prime.clear()
    elseif id then
        Prime.arm(frame, panel, id)
    elseif Prime.armedAt then
        Prime.verdict(frame, panel)
    else
        emit("T35 — raid true-filtering probe (SetSearchToActivity + secure refresh):")
        emit("  /euidiag prime list           — activityIDs for the open browse category")
        emit("  /euidiag prime <activityID>   — baseline, arm the probe button, prime")
        emit("  /euidiag prime                — verdict (after clicking the probe)")
        emit("  /euidiag prime clear          — un-prime, then click + verdict again")
    end
end

-------------------------------------------------------------------------------
-- Cast capture (event stream observer, toggled)
-------------------------------------------------------------------------------
local CAST_EVENTS = {
    "UNIT_SPELLCAST_START", "UNIT_SPELLCAST_STOP", "UNIT_SPELLCAST_SUCCEEDED",
    "UNIT_SPELLCAST_CHANNEL_START", "UNIT_SPELLCAST_CHANNEL_STOP",
    "UNIT_SPELLCAST_INTERRUPTIBLE", "UNIT_SPELLCAST_NOT_INTERRUPTIBLE",
}
local castCaptureOn = false

local function castWatcher(self, event, unit, ...)
    if type(unit) ~= "string" then return end
    if not (unit == "target" or unit == "focus"
        or unit:find("^nameplate") or unit:find("^boss") or unit:find("^party")) then
        return
    end
    local parts = { format("%.1f %s %s", GetTime() % 1000, event:gsub("UNIT_SPELLCAST_", ""), unit) }
    for i = 1, select("#", ...) do
        parts[#parts + 1] = classify((select(i, ...)))
    end
    emit(table.concat(parts, " | "))
end

local function setCastCapture(on)
    if on == castCaptureOn then return end
    castCaptureOn = on
    for _, ev in ipairs(CAST_EVENTS) do
        if on then
            local ok = pcall(function() Diag:RegisterEvent(ev, castWatcher) end)
            if not ok then outf("could not register %s", ev) end
        else
            pcall(function() Diag:UnregisterEvent(ev) end)
        end
    end
    emit("cast capture " .. (on and "ON — payloads will stream to chat (castBarID hunt)" or "OFF"))
end

-------------------------------------------------------------------------------
-- Death watch: confirm UnitIsDead flips false->true on a hostile mob at death.
-- T21 confirmed the return is a plain bool, but not the transition (the plate
-- is often removed before we poll). Hook removal + health/flags edges to catch
-- the exact moment and see what UnitIsDead reads then.
-------------------------------------------------------------------------------
local DEATH_EVENTS = { "NAME_PLATE_UNIT_ADDED", "NAME_PLATE_UNIT_REMOVED", "UNIT_HEALTH", "UNIT_FLAGS" }
local deathWatchOn = false
local deadLogged = {}

local function isWatchable(unit)
    return unit:find("^nameplate") or unit:find("^boss") or unit == "target"
end

local function deathWatcher(self, event, unit)
    if type(unit) ~= "string" then return end
    if event == "NAME_PLATE_UNIT_ADDED" then
        deadLogged[unit] = nil
        return
    end
    if not isWatchable(unit) then return end
    if event == "NAME_PLATE_UNIT_REMOVED" then
        -- the key moment: what does UnitIsDead report as the plate disappears?
        emit(format("%.1f REMOVED %-11s IsDead=%s Exists=%s Corpse=%s", GetTime() % 1000, unit,
            classify(select(2, try(UnitIsDead, unit))),
            classify(select(2, try(UnitExists, unit))),
            classify(select(2, try(UnitIsCorpse, unit)))))
        deadLogged[unit] = nil
        return
    end
    -- UNIT_HEALTH / UNIT_FLAGS: catch the false->true flip (deduped per unit)
    local ok, dead = try(UnitIsDead, unit)
    if ok and dead == true and not deadLogged[unit] then
        deadLogged[unit] = true
        emit(format("%.1f DIED    %-11s UnitIsDead flipped TRUE (via %s)",
            GetTime() % 1000, unit, event:gsub("UNIT_", "")))
    end
end

local function setDeathWatch(on)
    if on == deathWatchOn then return end
    deathWatchOn = on
    wipe(deadLogged)
    for _, ev in ipairs(DEATH_EVENTS) do
        if on then
            local ok = pcall(function() Diag:RegisterEvent(ev, deathWatcher) end)
            if not ok then outf("could not register %s", ev) end
        else
            pcall(function() Diag:UnregisterEvent(ev) end)
        end
    end
    emit("death watch " .. (on and "ON — kill mobs and watch for DIED / REMOVED lines" or "OFF"))
end

-------------------------------------------------------------------------------
-- Timeline injection probe (explicit command; adds a visible test bar)
-------------------------------------------------------------------------------
local function timelineInject()
    if not (C_EncounterTimeline and C_EncounterTimeline.AddScriptEvent) then
        result("FAIL", "AddScriptEvent", "API missing")
        return
    end
    -- EncounterTimelineScriptEventRequest (EncounterTimelineDocumentation.lua):
    -- required spellID + iconFileID + duration; optional maxQueueDuration(0),
    -- overrideName(""), icons, severity("Medium"), paused(false)
    -- Enum.EncounterEventSeverity: Low=0 Medium=1 High=2 (numeric — the
    -- string form "Medium" is rejected by validation)
    local sevHigh = (Enum.EncounterEventSeverity and Enum.EncounterEventSeverity.High) or 2
    local shapes = {
        { spellID = 8936, iconFileID = 134400, duration = 15,
          overrideName = "EUI Diag Test", severity = sevHigh },
        { spellID = 8936, iconFileID = 134400, duration = 15 },
    }
    for i, req in ipairs(shapes) do
        local ok, r = pcall(C_EncounterTimeline.AddScriptEvent, req)
        outf("AddScriptEvent shape %d -> %s", i, ok and ("ok, returned " .. classify(r)) or ("ERROR: " .. tostring(r)))
        if ok then
            if r ~= nil and not isSecret(r) then
                -- immediate read-back: is our own script event visible/plain?
                local okSt, st = try(C_EncounterTimeline.GetEventState, r)
                outf("  GetEventState=%s", okSt and classify(st) or ("ERR " .. tostring(st)))
                local okRem, rem = try(C_EncounterTimeline.GetEventTimeRemaining, r)
                outf("  GetEventTimeRemaining=%s", okRem and classify(rem) or ("ERR " .. tostring(rem)))
                local okTr, track, sortIdx = try(C_EncounterTimeline.GetEventTrack, r)
                outf("  GetEventTrack=%s sortIndex=%s", okTr and classify(track) or ("ERR " .. tostring(track)), classify(sortIdx))
                local okI, info = try(C_EncounterTimeline.GetEventInfo, r)
                if okI and info then dumpFields("  info", info) else outf("  GetEventInfo ERR/nil: %s", tostring(info)) end
                local function inList(label, okL, lst)
                    if okL and type(lst) == "table" then
                        local found = false
                        for _, id in ipairs(lst) do
                            if not isSecret(id) and id == r then found = true break end
                        end
                        outf("  %s: %d event(s), contains ours=%s", label, #lst, tostring(found))
                    end
                end
                inList("GetEventList", try(C_EncounterTimeline.GetEventList))
                inList("GetSortedEventList(unfiltered)", try(C_EncounterTimeline.GetSortedEventList, nil, nil, false, false))
                if C_EncounterTimeline.CancelScriptEvent and C_Timer then
                    C_Timer.After(20, function() pcall(C_EncounterTimeline.CancelScriptEvent, r) end)
                    emit("  (auto-cancel in 20s — check if a bar is visible on the Blizzard timeline HUD)")
                end
            end
            break
        end
    end
end

-------------------------------------------------------------------------------
--  Spell-ID NeverSecret whitelist hunt
-------------------------------------------------------------------------------
-- Whitelist hunt: sweep GetSpellAuraSecrecy/GetSpellCastSecrecy over all
-- spell IDs to enumerate the NeverSecret whitelist (keyed by APPLIED-AURA id
-- — 119611 Renewing Mist HoT is never while cast id 115151 is contextual)
-------------------------------------------------------------------------------
local huntState
local function whitelistHunt(startID, stopID)
    if not (C_Secrets and C_Secrets.GetSpellAuraSecrecy) then
        result("FAIL", "hunt", "C_Secrets.GetSpellAuraSecrecy missing")
        return
    end
    if huntState then
        emit("hunt already running (" .. huntState.next .. "/" .. huntState.stop .. ")")
        return
    end
    startID = startID or 1
    stopID = stopID or 500000
    huntState = { next = startID, stop = stopID, auraHits = {}, castHits = {}, spells = 0 }
    outf("whitelist hunt: scanning spell IDs %d..%d in background chunks...", startID, stopID)

    local exists = C_Spell and C_Spell.DoesSpellExist
    local CHUNK = 4000
    local function step()
        local st = huntState
        if not st then return end
        local last = math.min(st.next + CHUNK - 1, st.stop)
        for id = st.next, last do
            -- gate on real spells: unknown ids must not pollute the hit list
            if not exists or exists(id) then
                st.spells = st.spells + 1
                local ok, lvl = pcall(C_Secrets.GetSpellAuraSecrecy, id)
                if ok and lvl == 0 then st.auraHits[#st.auraHits + 1] = id end
                if C_Secrets.GetSpellCastSecrecy then
                    local ok2, lvl2 = pcall(C_Secrets.GetSpellCastSecrecy, id)
                    if ok2 and lvl2 == 0 then st.castHits[#st.castHits + 1] = id end
                end
            end
        end
        st.next = last + 1
        if st.next > st.stop then
            outf("hunt done: %d real spells in range; aura=never: %d, cast=never: %d",
                st.spells, #st.auraHits, #st.castHits)
            local function report(label, hits)
                outf("%s (%d):", label, #hits)
                local cap = 1500
                for i = 1, math.min(#hits, cap) do
                    local id = hits[i]
                    local name = C_Spell and C_Spell.GetSpellName and C_Spell.GetSpellName(id)
                    outf("  %d %s", id, tostring(name or ""))
                end
                if #hits > cap then
                    outf("  ... +%d more — re-run with a narrower range (/euidiag hunt %d %d)",
                        #hits - cap, hits[cap + 1], huntState and huntState.stop or stopID)
                end
            end
            report("aura=NeverSecret", st.auraHits)
            report("cast=NeverSecret", st.castHits)
            emit("/euidiag copy to export")
            huntState = nil
        else
            if (st.next - startID) % 40000 == 0 then
                outf("  ...scanned to id %d (%d real spells, %d aura hits so far)",
                    st.next - 1, st.spells, #st.auraHits)
            end
            C_Timer.After(0, step)
        end
    end
    step()
end

-------------------------------------------------------------------------------
-- Charge watch: the two timers of a charge spell, sampled side by side.
--
-- Blizzard's Cooldown Manager collapses a charge spell onto ONE cooldown
-- widget, and at zero charges it drops the recharge timer entirely:
-- CheckCacheCooldownValuesFromCharges only claims the widget while
-- currentCharges > 0, so a spell that is out of charges is drawn from
-- C_Spell.GetSpellCooldown instead. Action bars never do this -- they keep a
-- separate chargeCooldown widget driven from C_Spell.GetSpellCharges.
--
-- That makes the two timers observable independently, which is what this
-- watches. Print one line per state change (not per frame) so a ten second
-- window reads as a short transition list:
--
--   hover the Judgment icon in the Cooldown Manager, then:
--   /euidiag chargewatch 20271     start on Judgment
--   /euidiag chargewatch off       stop
--
-- The hover is how the item frame is identified: item:GetSpellID() reads SECRET
-- to a tainted addon, so it cannot be matched against the ID being watched.
-- Skipping the hover still logs the API reads, which carry the finding.
--
-- Read the log for the moment cd.onGCD flips true while charges are still
-- exhausted: that is the spell cooldown being replaced by the global, and it
-- is what un-greys the icon and restarts the swipe a global early.
--
-- currentCharges is classified, never compared, so this is safe to run in
-- instanced combat where it reads SECRET. The clean fields (isActive, onGCD,
-- maxCharges, and both start/duration pairs) carry the finding on their own.
-------------------------------------------------------------------------------
local chargeWatch = nil

-- The Cooldown Manager item frame showing this spell. It cannot be found by
-- searching: item:GetSpellID() returns a SECRET number to a tainted addon, so
-- matching it against the watched ID is exactly the comparison the secret
-- system forbids. Take the frame from the mouse instead -- pointing at an icon
-- is clean, unambiguous, and needs no ID at all.
local function pickCdmItem()
    local foci
    if GetMouseFoci then
        foci = GetMouseFoci()
    elseif GetMouseFocus then
        foci = { GetMouseFocus() }
    end
    if type(foci) ~= "table" then return end
    for _, f in ipairs(foci) do
        -- Walk up: the mouse usually lands on a child region of the item.
        local node, hops = f, 0
        while node and hops < 6 do
            if node.GetSpellID and node.GetCooldownID then
                local name = node.GetName and node:GetName()
                local parent = node.GetParent and node:GetParent()
                local pname = parent and parent.GetName and parent:GetName()
                return node, name or pname or "unnamed item"
            end
            node = node.GetParent and node:GetParent()
            hops = hops + 1
        end
    end
end

-- Remaining seconds of a start/duration pair, or nil when nothing is running.
-- Both fields can be SECRET in instanced combat; the caller only ever formats
-- the result, so hand back a string.
local function remain(start, duration)
    if isSecret(start) or isSecret(duration) then return "SECRET" end
    if type(start) ~= "number" or type(duration) ~= "number" then return "?" end
    if start == 0 or duration == 0 then return "-" end
    local left = start + duration - GetTime()
    if left < 0 then left = 0 end
    return format("%.2f", left)
end

-- Every value below goes through classify, never tostring. tostring does NOT
-- launder a secret -- it hands back a secret string, which then blows up in
-- table.concat and string.format one call later. classify is the only safe
-- stringifier here, and it reports a secret as the plain word SECRET.
local function chargeSample()
    local w = chargeWatch
    if not w then return end
    local cd = C_Spell.GetSpellCooldown and C_Spell.GetSpellCooldown(w.spellID)
    local ch = C_Spell.GetSpellCharges and C_Spell.GetSpellCharges(w.spellID)
    local item = w.item

    local cdActive   = classify(cd and cd.isActive)
    local cdOnGCD    = classify(cd and cd.isOnGCD)
    local chActive   = classify(ch and ch.isActive)
    local actualCD   = classify(item and item.isOnActualCooldown)
    local fromCharge = classify(item and item.wasSetFromCharges)
    local desat      = classify(item and item.cooldownDesaturated)

    -- Signature of the state, not of the clock. Durations are in it because a
    -- cooldown being REPLACED (recharge -> global) changes them, while ordinary
    -- ticking does not, so this prints a line per transition rather than per
    -- sample. A field that reads SECRET classifies to a constant and so drops
    -- out of the signature -- the clean fields have to carry the transitions.
    local sig = table.concat({
        cdActive, cdOnGCD, chActive, actualCD, fromCharge, desat,
        classify(cd and cd.duration), classify(ch and ch.cooldownDuration),
    }, "/")
    if sig == w.sig then return end
    w.sig = sig

    outf("%6.2f cd[active=%s gcd=%s left=%s dur=%s] ch[active=%s left=%s max=%s cur=%s] item[actualCD=%s fromCharges=%s desat=%s]",
        GetTime() - w.t0,
        cdActive, cdOnGCD,
        remain(cd and cd.startTime, cd and cd.duration), classify(cd and cd.duration),
        chActive,
        remain(ch and ch.cooldownStartTime, ch and ch.cooldownDuration),
        classify(ch and ch.maxCharges), classify(ch and ch.currentCharges),
        actualCD, fromCharge, desat)
end

local function setChargeWatch(spellID)
    if chargeWatch then
        chargeWatch.ticker:Cancel()
        chargeWatch = nil
        emit("charge watch OFF")
        if not spellID then return end
    end
    if not spellID then
        emit("charge watch: give a spell ID, e.g. /euidiag chargewatch 20271")
        return
    end
    local ch = C_Spell.GetSpellCharges and C_Spell.GetSpellCharges(spellID)
    if not ch then
        outf("charge watch: %d reports no charge data -- is it a charge spell?", spellID)
        return
    end
    local item, label = pickCdmItem()
    chargeWatch = { spellID = spellID, t0 = GetTime(), item = item }
    -- 20 Hz: a global cooldown is 12+ samples wide, which is enough to place the
    -- flip inside it, and the emit-on-change gate keeps the log short.
    chargeWatch.ticker = C_Timer.NewTicker(0.05, chargeSample)
    outf("charge watch ON for %d (%s)", spellID,
        classify(C_Spell.GetSpellName and C_Spell.GetSpellName(spellID)))
    if item then
        outf("  CDM item taken from the mouse: %s", label)
        outf("  item.isOnActualCooldown reads %s", classify(item.isOnActualCooldown))
        outf("  item.wasSetFromCharges reads %s", classify(item.wasSetFromCharges))
    else
        emit("  no CDM item under the mouse -- the item[] columns will be nil.")
        emit("  hover the icon in the Cooldown Manager and re-run to capture them.")
    end
    emit("spend every charge, then press another ability just before the last one returns")
    chargeSample()
end

-------------------------------------------------------------------------------
--  Commands
-------------------------------------------------------------------------------
-- No group, so Core keeps these out of the everyday help and lists them only
-- under `/euidiag help all`.
local ROUND2 = { "hp", "dead", "classify", "icon", "portrait", "widthfp",
                 "threat", "reveal", "widgets", "catalog", "dr", "ping" }

ns.Command("round2", {
    usage = "round2",
    help  = "the 2026-07-07 inference-trick battery",
    fn    = function()
        emit(("="):rep(60))
        outf("round-2 inference-trick battery — %s", date("%Y-%m-%d %H:%M:%S"))
        for _, key in ipairs(ROUND2) do ns.RunProbe(key) end
        emit("done. /euidiag copy for a paste-friendly log")
    end,
})

ns.Command("secureclick", {
    usage = "secureclick",
    help  = "T33: does secure click-forwarding launder taint (arm, click, re-run)",
    fn    = function() secureClickRun("plain", "T33") end,
})

ns.Command("preclick", {
    usage = "preclick",
    help  = "T34: the same, with a PreClick handler doing option-toggle work",
    fn    = function() secureClickRun("preclick", "T34") end,
})

ns.Command("prime", {
    usage = "prime [list|<activityID>|clear]",
    help  = "T35: raid SetSearchToActivity plus a secure refresh",
    fn    = function(args) Prime.run(args[1]) end,
})

ns.Command("casts", {
    usage = "casts [on|off]",
    help  = "stream UNIT_SPELLCAST payloads (castBarID hunt)",
    fn    = function(args)
        local arg = (args[1] or ""):lower()
        setCastCapture(arg == "on" or (arg ~= "off" and not castCaptureOn))
    end,
})

ns.Command("deathwatch", {
    usage = "deathwatch [on|off]",
    help  = "log UnitIsDead flip and plate removal at mob death",
    fn    = function(args)
        local arg = (args[1] or ""):lower()
        setDeathWatch(arg == "on" or (arg ~= "off" and not deathWatchOn))
    end,
})

ns.Command("inject", {
    usage = "inject",
    help  = "add a test bar to Blizzard's encounter timeline",
    fn    = function() timelineInject() end,
})

ns.Command("chargewatch", {
    usage = "chargewatch [<spellID>|off]",
    help  = "log the spell cooldown and the charge recharge side by side",
    fn    = function(args)
        local arg = (args[1] or ""):lower()
        setChargeWatch(arg ~= "off" and tonumber(args[1]) or nil)
    end,
})

ns.Command("hunt", {
    usage = "hunt [start] [stop]",
    help  = "sweep every spell ID for the NeverSecret aura/cast whitelist (slow)",
    fn    = function(args) whitelistHunt(tonumber(args[1]), tonumber(args[2])) end,
})
