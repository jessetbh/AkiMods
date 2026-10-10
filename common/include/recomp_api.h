#ifndef __RECOMP_API_H__
#define __RECOMP_API_H__

// Runtime functions this mod imports (subset of the MMRecompModTemplate headers, CC0).
#include "modding.h"

RECOMP_IMPORT("*", unsigned long recomp_get_config_u32(const char* key));
RECOMP_IMPORT("*", double recomp_get_config_double(const char* key));
RECOMP_IMPORT("*", char* recomp_get_config_string(const char* key));
// Frees a value returned by recomp_get_config_string. MUST be called to prevent a leak.
RECOMP_IMPORT("*", void recomp_free_config_string(char* str));
// No recomp_printf: the AKI runtimes don't export it (Zelda's game code does), and
// importing a missing function makes the mod fail to load.

#endif
