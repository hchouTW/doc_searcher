"""Smoke-test the optional Hammerspoon watcher with a minimal Lua API stand-in."""

import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "examples/center_on_screen/hammerspoon/init.lua"
LUA = shutil.which("luajit") or shutil.which("lua")


@pytest.mark.skipif(LUA is None, reason="Lua runtime is not installed")
def test_new_standard_windows_move_once_without_resizing_oversized_windows():
    smoke = r"""
local windows, moves = {}, {}
local filter = { windowCreated = 'created', windowDestroyed = 'destroyed' }
function filter.new()
  return { subscribe = function(self, event, callback)
    self[event] = callback
    return self
  end }
end
hs = {
  window = { filter = filter, get = function(id) return windows[id] end },
  timer = { doAfter = function(_, callback) callback() end },
  axuielement = { windowElement = function()
    return { isAttributeSettable = function() return true end }
  end },
  printf = function() end,
}
local function makeWindow(id, width, height, fullscreen)
  local window = {
    id = function() return id end,
    isVisible = function() return true end,
    isStandard = function() return true end,
    isMinimized = function() return false end,
    isFullScreen = function() return fullscreen or false end,
    screen = function() return { frame = function()
      return {x=-1920, y=30, w=1440, h=900}
    end } end,
    frame = function() return {x=0, y=0, w=width, h=height} end,
    setTopLeft = function(_, point) moves[#moves+1] = point end,
  }
  windows[id] = window
  return window
end
dofile(arg[1])
local normal = makeWindow(1, 801, 501)
centerOnScreenFilter.created(normal, 'Finder')
assert(#moves == 1 and moves[1].x == -1600 and moves[1].y == 230)
centerOnScreenFilter.created(normal, 'Finder')
assert(#moves == 1)
centerOnScreenFilter.created(makeWindow(2, 1600, 1000), 'Finder')
assert(#moves == 2 and moves[2].x == -1920 and moves[2].y == 30)
centerOnScreenFilter.created(makeWindow(3, 700, 400, true), 'Finder')
assert(#moves == 2)
local gone = makeWindow(4, 700, 400)
windows[4] = nil
centerOnScreenFilter.created(gone, 'Finder')
assert(#moves == 2)
"""
    result = subprocess.run(
        [LUA, "-", str(SCRIPT)],
        input=smoke,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
