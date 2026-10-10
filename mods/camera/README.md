# Camera (`aki_camera`)

A keyboard + mouse free camera for all six games. **F6** takes the camera from the game:

| Input | Action |
|---|---|
| Mouse | Look around |
| Mouse wheel | Move in / out along the view |
| W / S | Move up / down relative to the camera (tilts with the view) |
| A / D | Move left / right |
| Space / Ctrl | Move straight up / down |
| Shift | Move 4x faster |
| F6 again | Give the camera back to the game |

It starts from the game's current view, so taking it doesn't jump. While it's on, the game's
keyboard bindings are suppressed (controllers still play the game) and the cursor is locked to
the window; opening the menu frees it. Mods menu options: mouse sensitivity, move speed, invert Y.

Needs the keyboard/mouse host exports (`recomp_get_key_down`, `recomp_take_mouse_motion`,
`recomp_get_mouse_dx/dy/wheel`, `recomp_set_mouse_captured`, `recomp_set_keyboard_suppressed`,
see `common/include/recomp_input.h`) in each game's `src/main/main.cpp`, backed by RecompFrontend
`wcw` 10db9f8 (keyboard suppression, mod cursor lock, raw mouse motion).

## How it works

All six games set up their camera the same way (shown for No Mercy, `func_8000E098`):

```
func_800359D0(0x80069850, ..., 33.0, 4/3, 16.0, 8192.0, 1.0)   // guPerspectiveF -> proj
func_80035040(0x80069890, eye xyz, at xyz, up 0,1,0)            // guLookAtF      -> view
func_80001580(gfx, proj, view)                                  // CAMERA_MATRIX = view x proj
```

Every model is then drawn with `model x CAMERA_MATRIX`. The mod hooks `CAMERA_FUNC` on entry
(records `proj` and `view`) and, with the free camera on, rebuilds the matrix on return from its
own eye and angles:

```
CAMERA_MATRIX = look_at(eye, eye + forward) x proj
```

Taking the camera copies the game's eye and direction from its view matrix (`capture_game_view`);
`tests/run.ps1` checks that this round-trips and that the movement directions are right.

| Game | `CAMERA_FUNC` | `CAMERA_MATRIX` |
|---|---|---|
| World Tour | `func_800016F8` | `0x80042C80` |
| Revenge | `func_800014AC` | `0x80048288` |
| WrestleMania 2000 | `func_800016A8` | `0x80057198` |
| VPW64 | `func_800016FC` | `0x800544F0` |
| No Mercy | `func_80001580` | `0x80062BA8` |
| VPW2 | `func_80001768` | `0x80056F78` |

`CAMERA_FUNC` is a 3-instruction wrapper (a2 -> a0, `lui a2, <matrix>`, `jal <float 4x4 multiply>`).
The 1997-engine games (WT, Revenge, VPW64) were found by searching the disassembly for that shape.

## Limits

- The game culls with its own camera, so flying far from it can show unfinished arena edges or make
  things pop in.
- Taking the camera during a logo or menu screen captures whatever camera that screen had; take it
  once the 3D scene is up.
- Ray tracing reconstructs the scene from the game's camera, so its shadows won't match the free
  camera's view.
