# Center windows on screen

DocSearcher centers its own main window at startup on the display under the pointer, or the primary display if the pointer is outside every display. It uses the display's usable area and keeps the window's size. This works independently of the optional scripts below. The scripts affect **other applications** only when you install and run them; the packaged DocSearcher app is excluded. Installing DocSearcher does not activate global window management.

An oversized window retains its dimensions and starts at the work area's top-left corner so its title bar stays reachable. Maximized and fullscreen windows are not moved.

## Which tool to use

| System | Tool | Behavior |
| --- | --- | --- |
| macOS | [Hammerspoon](https://www.hammerspoon.org/) | The event-driven script below centers each eligible new window once. |
| macOS | [Rectangle](https://rectangleapp.com/) | Keyboard shortcuts and snap areas move windows on demand. A center shortcut alone does not automate every new window. |
| macOS | [Magnet](https://magnet.crowdcafe.com/) | Menu, shortcuts, and drag targets place windows on demand. A custom automation would be needed for this every-new-window behavior. |
| macOS | [Raycast Window Management](https://manual.raycast.com/window-management) | Its Center action centers the focused window without resizing. Invoking it manually is different from watching new windows. |
| Windows | AutoHotkey v2 | The event-driven script below centers each eligible newly shown window once. |
| Windows | [PowerToys FancyZones](https://learn.microsoft.com/en-us/windows/powertoys/fancyzones) | It can move new windows to an app's **last known zone** or the current active monitor. That does not center every arbitrary new window without zone history. |

## macOS: Hammerspoon

1. Install [Hammerspoon](https://www.hammerspoon.org/) and grant it **Accessibility** access in macOS System Settings → Privacy & Security → Accessibility.
2. Save the script below as `~/.hammerspoon/init.lua` (also available as [init.lua](../examples/center_on_screen/hammerspoon/init.lua)). If you already have an `init.lua`, append the script to it.
3. Choose **Reload Config** from the Hammerspoon menu. Enable **Launch at Login** in Hammerspoon if you want it to run after sign-in.
4. To stop the behavior, remove the script from `init.lua` and reload the config, or quit Hammerspoon. Disable Launch at Login to prevent future startup.

The watcher uses the newly created window's current display, including displays with negative coordinates. It ignores known system apps, transient and nonstandard windows, very small windows, fullscreen and near-fullscreen frames, and windows whose accessibility position cannot be changed. A later user drag is not undone.

```lua
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
```

## Windows 10/11: AutoHotkey v2

1. Install [AutoHotkey v2](https://www.autohotkey.com/) (v1 cannot run this script).
2. Save the script below as `center_on_screen.ahk` (also available as [center_on_screen.ahk](../examples/center_on_screen/center_on_screen.ahk)) and double-click it. Confirm its tray icon appears.
3. Use the tray icon to **Reload** after editing or **Exit** to stop it. To start at sign-in, press `Win+R`, enter `shell:startup`, and put a shortcut to the `.ahk` file in that folder. Remove the shortcut to disable sign-in launch.

The script chooses the monitor containing the largest portion of each new window. If no monitor overlaps, it chooses the nearest one. It uses that monitor's work area, including negative coordinates, and retains the window's dimensions. It skips system, tool, popup, borderless, hidden, cloaked, minimized, maximized, and fullscreen-like windows. Windows elevated above the script's privilege level may refuse movement; the watcher continues.

```autohotkey
; Purpose: Optional automatic centering for new Windows 10/11 app windows.
; What it does: Watches top-level show events and moves eligible windows once
; inside their monitor's work area, retaining their existing dimensions.
; Usage: Install AutoHotkey v2, run this file, and confirm its tray icon.
; Reload or Exit from that icon. To launch at sign-in, put a shortcut to this
; file in Win+R -> shell:startup; remove the shortcut to opt out.
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; Keep monitor rectangles and WinGetPos in the same physical-pixel coordinate
; system on mixed-DPI desktops. Windows 10/11 support this DPI context.
DllCall("user32\SetThreadDpiAwarenessContext", "Ptr", -4, "Ptr")

seen := Map()
callback := CallbackCreate(WinEvent, , 7)
; EVENT_OBJECT_DESTROY through EVENT_OBJECT_SHOW; ignore other events below.
hook := DllCall("user32\SetWinEventHook", "UInt", 0x8001, "UInt", 0x8002,
    "Ptr", 0, "Ptr", callback, "UInt", 0, "UInt", 0, "UInt", 0,
    "Ptr")
if !hook {
    CallbackFree(callback)
    MsgBox("Could not install the window event hook.", "Center on Screen")
    ExitApp()
}
OnExit(Cleanup)

Cleanup(*) {
    global hook, callback
    DllCall("user32\UnhookWinEvent", "Ptr", hook)
    CallbackFree(callback)
}

WinEvent(_hook, event, hwnd, idObject, idChild, _thread, _time) {
    global seen
    if (idObject != 0 || idChild != 0 || !hwnd)
        return
    if (event = 0x8001) {
        if seen.Has(hwnd)
            seen.Delete(hwnd)
        return
    }
    if (event != 0x8002 || seen.Has(hwnd))
        return
    seen[hwnd] := true
    ; A show event can precede the final frame. The one-shot timer also keeps
    ; slow accessibility and window calls out of the event callback.
    SetTimer(() => TryCenter(hwnd, 1), -200)
}

TryCenter(hwnd, attempt) {
    try {
        if !DllCall("user32\IsWindow", "Ptr", hwnd, "Int")
            return
        if !DllCall("user32\IsWindowVisible", "Ptr", hwnd, "Int")
            return
        if (DllCall("user32\GetAncestor", "Ptr", hwnd, "UInt", 2, "Ptr") != hwnd)
            return
        if DllCall("user32\GetWindow", "Ptr", hwnd, "UInt", 4, "Ptr")
            return ; owned dialogs and floating windows

        style := WinGetStyle("ahk_id " hwnd)
        exStyle := WinGetExStyle("ahk_id " hwnd)
        if (style & 0x40000000 || style & 0x80000000 || !(style & 0x00C00000))
            return ; child, popup, or borderless
        if (exStyle & 0x00000080 || exStyle & 0x00000008)
            return ; tool or always-on-top overlay
        if (WinGetMinMax("ahk_id " hwnd) != 0)
            return
        class := WinGetClass("ahk_id " hwnd)
        if (class = "Progman" || class = "WorkerW" || class = "Shell_TrayWnd"
            || class = "Shell_SecondaryTrayWnd")
            return
        if (WinGetProcessName("ahk_id " hwnd) = "DocSearcher.exe")
            return ; the packaged app already centers its own window

        cloaked := Buffer(4, 0)
        if (DllCall("dwmapi\DwmGetWindowAttribute", "Ptr", hwnd, "UInt", 14,
            "Ptr", cloaked, "UInt", 4, "Int") = 0 && NumGet(cloaked, 0, "UInt"))
            return
        WinGetPos(&x, &y, &width, &height, "ahk_id " hwnd)
        if (width < 180 || height < 100) {
            if (attempt < 5)
                SetTimer(() => TryCenter(hwnd, attempt + 1), -200)
            return
        }
        monitor := BestMonitor(x, y, width, height)
        if !monitor
            return
        MonitorGetWorkArea(monitor, &left, &top, &right, &bottom)
        workWidth := right - left, workHeight := bottom - top
        if (width <= workWidth && height <= workHeight
            && width >= workWidth * 0.95 && height >= workHeight * 0.95)
            return ; fullscreen-like overlays and borderless app windows
        ; Oversized windows keep their dimensions and start at the work-area
        ; top/left, leaving the title bar reachable.
        newX := left + Max(0, Floor((workWidth - width) / 2))
        newY := top + Max(0, Floor((workHeight - height) / 2))
        WinMove(newX, newY, , , "ahk_id " hwnd)
    } catch {
        ; The window may disappear, reject WinMove, or belong to an elevated
        ; app. A failure must not stop the hook or affect later windows.
    }
}

BestMonitor(x, y, width, height) {
    count := MonitorGetCount(), best := 0, largest := 0, nearest := 1.0e30
    Loop count {
        MonitorGet(A_Index, &left, &top, &right, &bottom)
        overlap := Max(0, Min(x + width, right) - Max(x, left))
            * Max(0, Min(y + height, bottom) - Max(y, top))
        dx := Max(left - (x + width), x - right, 0)
        dy := Max(top - (y + height), y - bottom, 0)
        distance := dx * dx + dy * dy
        if (overlap > largest || (largest = 0 && distance < nearest)) {
            best := A_Index, largest := overlap, nearest := distance
        }
    }
    return best
}
```

## Troubleshooting and limits

- **Nothing moves:** Check Hammerspoon Accessibility permission or the AutoHotkey v2 tray icon. Reload the script after changing it. Open a *new* standard app window; existing windows are intentionally untouched.
- **A window stays put:** Some protected, elevated, or app-controlled windows cannot be moved. These examples deliberately ignore dialogs, widgets, overlays, borderless windows, and system UI.
- **Wrong display or position:** Check the operating system's display arrangement, scaling, Dock, menu bar, and taskbar settings. The scripts use a new window's own display, while DocSearcher uses the pointer's display at startup. Reopen the window after changing monitor layout.
- **A window is larger than its display:** The scripts preserve its size and keep the title bar at the usable area's top edge; some content will remain off-screen.

## Validation record

The Qt geometry calculation is covered by offscreen tests for negative display origins, odd sizes, oversized windows, and primary-display fallback. Offscreen tests cannot verify native title-bar placement.

| Check (2026-09-30) | Result |
| --- | --- |
| Qt geometry tests on macOS | 3 passed |
| UI feature tests run alone on macOS | 41 passed after explicit popup teardown. |
| Full project test suite on macOS | 510 passed, 5 skipped, 2 expected failures |
| Native macOS frame placement | Actual DocSearcher main window centered within 1 logical pixel on the available display; tested with a temporary config and database. |
| Hammerspoon Lua syntax | Passed `luajit -e 'assert(loadfile("examples/center_on_screen/hammerspoon/init.lua"))'`. |
| Hammerspoon watcher smoke test | Passed with a Lua stand-in for placement, duplicate events, oversized windows, fullscreen windows, and disappearing windows. |
| Hammerspoon live-window checks | Not run: Hammerspoon is not installed on the development Mac. |
| Windows AutoHotkey v2 syntax | Passed in Windows CI (AutoHotkey 2.0.28 `/Validate`, checksum-verified). |
| Windows AutoHotkey v2 live-window checks | Passed in Windows CI on a GitHub-hosted runner desktop (single 1024x720 display, 100% scale, `tests/windows/center_on_screen_live.ps1`): a standard window is centered to the pixel with its size kept and is not re-centered after a user move; borderless, tool, and always-on-top windows are ignored; the script keeps running. Explorer, Notepad, browsers, mixed-scale or multiple monitors, widgets, and elevated apps were not exercised.

Before describing the examples as platform-verified, manually check macOS with Finder, a browser, an editor, a second window from an already-running app, an app launched after the watcher, a second monitor with different scaling, a menu/widget, fullscreen and borderless windows, and a drag after placement. On Windows, check Explorer, Notepad, a browser, mixed-scale monitors, widgets, and an elevated app. Check DocSearcher's first launch and subsequent opens on one and two monitors, with different Dock/taskbar positions, then confirm search, dialogs, CLI, and MCP still work.
