#ifndef __RECOMP_INPUT_H__
#define __RECOMP_INPUT_H__

// Controller, keyboard and mouse access the AKI ports export to mods (register_mod_input_exports
// in each game's src/main/main.cpp). Kept out of recomp_api.h: every import must exist when a mod
// loads, so only mods that use these should include this header.
#include "modding.h"

// Player 1's right stick, -1..1 with deadzone applied (SDL axes: +x right, +y down).
RECOMP_IMPORT("*", float recomp_get_right_analog_x(void));
RECOMP_IMPORT("*", float recomp_get_right_analog_y(void));
// Nonzero: stop the right stick driving the C buttons (their digital bindings still work).
RECOMP_IMPORT("*", void recomp_set_right_analog_suppressed(unsigned int suppressed));
// 1 while player 1's right stick is clicked (R3). R3 is also bound to C-up by default.
RECOMP_IMPORT("*", unsigned int recomp_get_right_stick_click(void));
// Rotates every player's movement input (the N64 d-pad in AKI games) by this many radians
// before the game sees it, snapped to 8 directions. 0 = unchanged.
RECOMP_IMPORT("*", void recomp_set_movement_rotation(float radians));

// 1 while the key with this SDL scancode is held (0 while a menu has the input). Reads the
// keyboard directly, so it works even with the game's keyboard bindings suppressed.
RECOMP_IMPORT("*", unsigned int recomp_get_key_down(unsigned int scancode));
// Latches the mouse motion (pixels, unscaled) and wheel clicks since the last call, read with
// the three getters below. Nothing accumulates while a menu has the input.
RECOMP_IMPORT("*", void recomp_take_mouse_motion(void));
RECOMP_IMPORT("*", float recomp_get_mouse_dx(void));
RECOMP_IMPORT("*", float recomp_get_mouse_dy(void));
RECOMP_IMPORT("*", int recomp_get_mouse_wheel(void));
// Nonzero: lock and hide the cursor in the game window (relative mouse) while game input is
// enabled; menus still get a normal cursor.
RECOMP_IMPORT("*", void recomp_set_mouse_captured(unsigned int captured));
// Nonzero: the game's keyboard bindings stop reaching the game (controllers still work).
RECOMP_IMPORT("*", void recomp_set_keyboard_suppressed(unsigned int suppressed));

#endif
