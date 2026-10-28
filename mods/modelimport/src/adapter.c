// One adapter per game, picked by the AKI_GAME_<KEY> define build.ps1 passes (one source list for every game).
#if defined(AKI_GAME_NOMERCY)
#include "adapters/nomercy.c"
#elif defined(AKI_GAME_VPW2)
#include "adapters/vpw2.c"
#else
#error "aki_modelimport has no adapter for this game yet (mods/modelimport/src/adapters/<game>.c)"
#endif
