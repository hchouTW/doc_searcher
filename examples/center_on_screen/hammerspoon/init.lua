-- Purpose: Optional automatic centering for newly created macOS app windows.
-- What it does: Watches standard windows and moves each eligible frame once to
-- the usable area of its current screen without changing its dimensions.
-- Usage: Install Hammerspoon, grant it macOS Accessibility permission, copy this
-- into ~/.hammerspoon/init.lua, then choose Reload Config from its menu. Enable
-- Hammerspoon's Launch at Login if wanted; remove this file/reload to opt out.

local windowFilter = hs.window.filter
local seen = {}
local ignoredApps = {
    ["Dock"] = true,
    ["SystemUIServer"] = true,
    ["Notification Center"] = true,
    ["Control Center"] = true,
    ["Hammerspoon"] = true,
    ["DocSearcher"] = true, -- the app already centers its own window
}

local function place(window, id, attempt)
    local ok, result = pcall(function()
        local current = hs.window.get(id)
        if not current or not current:isVisible() or not current:isStandard()
            or current:isMinimized() or current:isFullScreen() then
            return "skip"
        end
        local screen = current:screen()
        if not screen then return "retry" end
        local frame, area = current:frame(), screen:frame()
        if not frame or frame.w < 180 or frame.h < 100 then return "retry" end
        -- Near-fullscreen windows and overlays are left in their app's chosen state.
        if frame.w <= area.w and frame.h <= area.h
            and frame.w >= area.w * 0.95 and frame.h >= area.h * 0.95 then
            return "skip"
        end
        local ax = hs.axuielement.windowElement(current)
        if not ax or ax:isAttributeSettable("AXPosition") ~= true then return "skip" end
        -- setFrameInScreenBounds may resize oversized windows; setTopLeft cannot.
        local x = area.x + math.max(0, (area.w - frame.w) / 2)
        local y = area.y + math.max(0, (area.h - frame.h) / 2)
        current:setTopLeft({x = math.floor(x + 0.5), y = math.floor(y + 0.5)})
        return "placed"
    end)
    if not ok then
        hs.printf("center-on-screen: skipped window %s: %s", tostring(id), tostring(result))
        return
    end
    if result == "retry" and attempt < 3 then
        hs.timer.doAfter(0.2, function() place(window, id, attempt + 1) end)
    end
end

-- Retain this dedicated filter across config load. windowCreated also observes
-- second windows from running apps and windows from apps launched later.
centerOnScreenFilter = windowFilter.new()
centerOnScreenFilter:subscribe(windowFilter.windowCreated, function(window, appName)
    if ignoredApps[appName] then return end
    local ok, id = pcall(function() return window:id() end)
    if not ok or not id or seen[id] then return end
    seen[id] = true -- suppress duplicate notifications and later user drags
    hs.timer.doAfter(0.2, function() place(window, id, 1) end)
end)
centerOnScreenFilter:subscribe(windowFilter.windowDestroyed, function(window)
    local ok, id = pcall(function() return window:id() end)
    if ok and id then seen[id] = nil end
end)
