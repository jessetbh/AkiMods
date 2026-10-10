#ifndef __AKI_MOD_H__
#define __AKI_MOD_H__

// Shared helpers for AKI family mods. build.ps1 compiles every mod once per game and
// defines, from common/games.psd1:
//   AKI_FRAME_HOOK   the game's per-frame function (runs at least once per frame)
//   AKI_GAME_<KEY>   e.g. AKI_GAME_NOMERCY, for the rare game-specific branch

#include "modding.h"

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed char s8;
typedef signed short s16;
typedef signed int s32;

#define AKI_STRINGIFY_(x) #x
#define AKI_STRINGIFY(x) AKI_STRINGIFY_(x)

#ifdef AKI_FRAME_HOOK
// Runs the following function at least once per frame, on every game:
//     AKI_ON_FRAME void my_mod_on_frame(void) { ... }
#define AKI_ON_FRAME RECOMP_HOOK(AKI_STRINGIFY(AKI_FRAME_HOOK))
#endif

#endif
