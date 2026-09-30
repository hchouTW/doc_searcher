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
