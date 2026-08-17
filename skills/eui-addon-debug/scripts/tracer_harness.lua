-- Load a generated tracer outside the game and fire events at it.
--
--     lua5.1 tracer_harness.lua <path to the generated .lua>
--
-- A syntax check proves the file compiles. It does not prove the tracer prints
-- anything, and a tracer that loads silently and never fires looks exactly like
-- a tracer that was installed wrong -- which is a whole round trip through the
-- user and the client to find out.
--
-- Stubs only what the template touches. Anything the template starts using has
-- to be added here, which is the point: the harness fails loudly rather than
-- letting the tracer reach the client untested.

local target = ...
if not target then
    io.stderr:write("usage: tracer_harness.lua <tracer.lua>\n")
    os.exit(2)
end

local frames = {}
local output = {}

local function NewFrame()
    local frame = {scripts = {}, events = {}}
    function frame:SetScript(kind, fn) self.scripts[kind] = fn end
    function frame:RegisterEvent(event) self.events[event] = true end
    function frame:UnregisterEvent(event) self.events[event] = nil end
    frames[#frames + 1] = frame
    return frame
end

CreateFrame = function() return NewFrame() end
SlashCmdList = {}

local now = 100.0
GetTime = function() return now end

local realprint = print
print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
    output[#output + 1] = table.concat(parts, " ")
end

-- The client classifies some values in combat; the template guards on this and
-- the guard is the part most likely to be wrong.
local secrets = {}
issecretvalue = function(v) return secrets[v] == true end

local chunk = assert(loadfile(target))
chunk()

-- Advance a frame: the tracer counts frames with its own OnUpdate.
local function Tick()
    now = now + 0.016
    for _, f in ipairs(frames) do
        if f.scripts.OnUpdate then f.scripts.OnUpdate(f, 0.016) end
    end
end

local function Fire(event, ...)
    for _, f in ipairs(frames) do
        if f.events[event] and f.scripts.OnEvent then
            f.scripts.OnEvent(f, event, ...)
        end
    end
end

local function Toggle()
    for name, fn in pairs(SlashCmdList) do fn("") end
end

local function Has(pattern)
    for _, line in ipairs(output) do
        if line:find(pattern) then return line end
    end
    return nil
end

-- Every check runs, then the exit code reports. Stopping at the first failure
-- hides the checks after it, and the ones after it are not less important --
-- the secret-value check is last and is the one a live client charges most for
-- getting wrong. A tracer generated without --unit used to fail the unit-filter
-- check, exit here, and never reach it.
local failures = 0

local function Check(label, ok)
    if ok == nil then
        realprint("skip " .. label)
        return
    end
    realprint((ok and "ok   " or "FAIL ") .. label)
    if not ok then failures = failures + 1 end
end

Check("loads and announces itself", Has("loaded") ~= nil)

-- Silent until switched on: a tracer that prints from load spams the log for
-- however long it takes the user to reach the case.
Fire("UNIT_AURA", "player", {addedAuras = {{auraInstanceID = 1, spellId = 2}}})
Check("silent before the slash toggle", Has("ADD") == nil)

Toggle()
Tick()

-- The decisive shape: a replacement arrives as a remove and an add of a new
-- instance in one frame.
local secretID = setmetatable({}, {__tostring = function() error("secret") end})
secrets[secretID] = true
Fire("UNIT_AURA", "player", {
    addedAuras = {{auraInstanceID = 735, spellId = 188290}},
    removedAuraInstanceIDs = {731},
    updatedAuraInstanceIDs = {999},
})
Check("prints an add", Has("ADD") ~= nil)
Check("prints a remove", Has("REMOVE") ~= nil)
Check("prints an update", Has("UPDATE") ~= nil)
Check("carries a frame number", Has("f%d") ~= nil)

-- Everything above landed in one frame, so it must read as one frame.
local addLine, removeLine = Has("ADD"), Has("REMOVE")
Check("same frame reads the same on both edges",
    addLine:match("f(%d+)") == removeLine:match("f(%d+)"))

-- Only meaningful when the tracer was generated with --unit. Asserting it on a
-- tracer that declares no filter fails something that is working as asked.
local declaresFilter = false
do
    local fh = io.open(target)
    if fh then
        local text = fh:read("*a")
        fh:close()
        declaresFilter = text:match('local UNITFILTER = "') ~= nil
    end
end

Fire("UNIT_AURA", "target", {addedAuras = {{auraInstanceID = 4, spellId = 5}}})
Check("unit filter drops another unit",
    declaresFilter and (Has("inst=4") == nil) or nil)

secrets[secretID] = true
Fire("UNIT_AURA", "player", {addedAuras = {{auraInstanceID = secretID, spellId = 7}}})
Check("a classified value prints instead of raising", Has("<secret>") ~= nil)

realprint("")
realprint(#output .. " line(s) traced")
if failures > 0 then
    realprint(failures .. " check(s) failed")
    os.exit(1)
end
