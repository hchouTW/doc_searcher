# Center Windows on Screen — Task Plan

## Current status (2026-09-30)

The DocSearcher placement helper, both optional scripts, documentation, README links, and focused tests are implemented in the working tree. Local Qt and Lua tests pass, and native placement of DocSearcher's main window was checked on one macOS display. Windows CI validates AutoHotkey v2 syntax and runs a live centering check on a real runner desktop (single monitor, 100% scale). Hammerspoon passed live checks on one macOS display. Multi-monitor and mixed-DPI behavior on both OSes, and the remaining edge cases (fullscreen, widgets, elevated apps), still need target-OS checks before the acceptance criteria are fully verified. See [the validation record](../center-on-screen.md#validation-record).

## Goal

Center DocSearcher's main window when it opens on macOS and Windows 10/11, and publish optional Hammerspoon and AutoHotkey v2 examples for people who want the same behavior across other applications. The examples must preserve window size, use the usable area of the chosen display, and ignore transient or system windows.

## Scope and decisions

- **DocSearcher itself:** implement startup placement in PySide6. Requiring a global window manager just to position this app would make its behavior depend on software the user might not have.
- **Other applications:** ship opt-in scripts and setup instructions, not an installer or an always-running component in DocSearcher. The scripts are examples for global behavior and must not be enabled by installing DocSearcher.
- **Display choice:** center DocSearcher on the screen under the pointer at launch, falling back to the primary screen. For the global scripts, use the new window's current screen/monitor; on Windows, fall back to the nearest monitor if its frame is off-screen. Document this choice so “active display” is unambiguous.
- **Centering:** use the display's available/work area (excluding menu bar, Dock, and taskbar), retain the window's existing dimensions, and clamp an oversized window enough to keep its title bar reachable. Do not reposition maximized or fullscreen windows.

## Work items

### 1. Center the DocSearcher main window

**Files:** `src/doc_searcher/desktop/app.py`; add focused coverage in `tests/test_ui_features.py` or `tests/unit/desktop/`.

- [ ] Add a small startup placement helper called from `run_app()` after `MainWindow` is created and before/after `show()` as required by native frame geometry. Use `QGuiApplication.screenAt(QCursor.pos())`, `QScreen.availableGeometry()`, and `QWidget.frameGeometry()`/`move()`; confirm the decorated frame is centered on macOS and Windows rather than just the client area.
- [ ] Cover primary-screen fallback, a display with nonzero or negative origin, odd window dimensions (within one logical pixel), and a window larger than the available area with Qt offscreen tests. Keep the test deterministic by passing a screen rectangle to the geometry calculation rather than relying on a real pointer or monitor configuration.
- [ ] Manually check first launch on one monitor and two monitors, mixed scaling, taskbar/Dock position, and subsequent opens. Confirm search, dialogs, and the CLI/MCP modes behave as before.

### 2. Add an optional Hammerspoon script for macOS

**Files:** create `examples/center_on_screen/hammerspoon/init.lua`; document it in `docs/center-on-screen.md`.

- [ ] Subscribe a dedicated `hs.window.filter` to `windowCreated`; keep its watcher in a global/local retained variable so it survives configuration load. The subscription covers windows opened by already-running apps and apps launched later; an `hs.application.watcher.launched` callback may be added only if a real launch race is demonstrated.
- [ ] On each event, defer briefly until a usable frame exists, then check that the window still exists, is visible, standard, movable, and neither minimized nor fullscreen. Reject tiny/near-screen-size overlays, known desktop/system apps, and duplicate notifications. Use `window:screen()` and `window:centerOnScreen(screen, true, 0)` or an equivalent available-frame calculation. Handle inaccessible windows without stopping the watcher.
- [ ] Include comments in the Lua file for installation, macOS Accessibility permission, reloading `~/.hammerspoon/init.lua`, Hammerspoon's **Launch at Login**, and the reason for each filter. Document that some protected or app-controlled windows cannot be moved.
- [ ] Manually test Finder/browser/editor windows, an app launched after the watcher, a second window from an already-running app, a second monitor, a system menu/widget, fullscreen and borderless windows, and a user drag after placement. Only newly created windows should move once.

### 3. Add an optional AutoHotkey v2 script for Windows 10/11

**Files:** create `examples/center_on_screen/center_on_screen.ahk`; document it in `docs/center-on-screen.md`.

- [ ] Use `#Requires AutoHotkey v2.0` and a persistent `SetWinEventHook` callback for top-level window appearance (`EVENT_OBJECT_SHOW`, with `EVENT_OBJECT_CREATE` only if testing shows a gap). Retain the callback object and hook handle, queue the handle briefly until the window is ready, deduplicate by HWND, and unhook on exit. A lightweight timer-based new-window detector is an acceptable fallback if the hook proves unreliable; avoid repeated recentering of existing windows.
- [ ] Before moving, require a live, visible, unowned top-level window with a normal app frame and meaningful dimensions. Exclude cloaked, tool, shell, child, popup, borderless, minimized, maximized, and fullscreen windows; filter `idObject != OBJID_WINDOW` and `idChild != CHILDID_SELF`. Catch disappearing-window and `WinMove` errors without terminating the script.
- [ ] Select the monitor that contains most of the window (nearest monitor if none intersects), retrieve that monitor's **work area**, and calculate `x = left + (right - left - width) / 2` and `y = top + (bottom - top - height) / 2`. Preserve dimensions; account for negative monitor coordinates and per-monitor DPI. Define behavior for windows larger than the work area and elevated apps.
- [ ] Add usage steps: install AutoHotkey **v2**, save/run the `.ahk` file, verify the tray icon, reload/exit, and place a shortcut to the script in `Win+R` → `shell:startup` for sign-in launch. Explain how to disable the script by removing that shortcut. Test Explorer, Notepad, a browser, two monitors with different scale factors, widgets/overlays, and an elevated window.

### 4. Publish tool choices and validate the guidance

**Files:** `docs/center-on-screen.md`; add links from both language sections of `README.md`.

- [ ] Compare **Hammerspoon** (event-driven automation), **Rectangle**, **Magnet**, and **Raycast** (manual center actions) on macOS. State which products require a custom automation for *every new window*; do not claim that a center shortcut alone provides automatic placement.
- [ ] Compare **AutoHotkey v2** (event-driven script) and **PowerToys FancyZones** on Windows. Explain that FancyZones can move new windows to their **last known zone** or active monitor, which is not the same as centering every arbitrary window without prior zone history.
- [ ] Provide one complete copyable script for each OS, setup/startup instructions, opt-out instructions, known limitations, and a short troubleshooting section (permissions, protected/elevated apps, app-enforced positions, multi-monitor changes). Clarify that the scripts affect other apps and that DocSearcher's own centering works independently.
- [ ] Run the focused Qt test and the project's normal test command. Validate Lua syntax with an installed Lua/Hammerspoon runtime and AHK v2 syntax on Windows CI or a Windows machine. Record manual OS-specific results; macOS testing alone cannot establish Windows hook or DPI behavior.

## Acceptance criteria

1. DocSearcher's main window opens centered within the usable area of the selected display on macOS and Windows 10/11, with its current size retained and title bar reachable.
2. Hammerspoon centers each eligible newly created standard window once, including windows from apps launched after the watcher starts, while ignoring fullscreen, overlays, and system UI.
3. AutoHotkey v2 centers each eligible newly shown top-level window once using its monitor's current work area; hidden/background/system windows stay untouched.
4. Both examples survive a failed or disappearing window without stopping automation, support multiple displays, and include login startup and removal instructions.
5. Documentation distinguishes automatic behavior from manual center shortcuts and from FancyZones' last-known-zone behavior.

## Source notes

- [Qt screen selection and geometry](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QGuiApplication.html), [Qt window frame geometry](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QWidget.html)
- [Hammerspoon window filter events](https://www.hammerspoon.org/docs/hs.window.filter.html), [window centering and standard-window checks](https://www.hammerspoon.org/docs/hs.window.html)
- [Windows monitor selection](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-monitorfromwindow), [PowerToys FancyZones behavior](https://learn.microsoft.com/en-us/windows/powertoys/fancyzones)

The external scripts and Windows-specific checks need validation on their target OS before documentation claims they are verified.
