# Per-game facts shared by every mod. Keys are the game names build.ps1 accepts.
#   Repo       game repo folder next to this one (..\<Repo>)
#   GameId     the port's mod_game_id (src/main/main.cpp)
#   Syms       function symbol file, relative to the repo (RecompModTool's reference syms)
#   FrameHook  function called at least once per frame: the one function per game that calls
#              both osViGetCurrentFramebuffer and osViGetNextFramebuffer. WT/VPW64 (1997 engine)
#              have the scheduler's "frame swap done?" check; the rest a frame-swap wait.
@{
    wt      = @{ Title = 'WCW vs. nWo World Tour';      Repo = 'WcwNwoWorldTour';  GameId = 'wcwnwoworldtour'; Syms = 'WCWSyms\dump.toml'; FrameHook = 'func_80001078' }
    revenge = @{ Title = 'WCW/nWo Revenge';             Repo = 'WcwRevengeRecomp'; GameId = 'wcwnworevenge';   Syms = 'syms\dump.toml';    FrameHook = 'func_800011D4' }
    wm2k    = @{ Title = 'WWF WrestleMania 2000';       Repo = 'Wm2kRecomp';       GameId = 'wwfwm2k';         Syms = 'syms\dump.toml';    FrameHook = 'func_80001380' }
    vpw64   = @{ Title = 'Virtual Pro Wrestling 64';    Repo = 'Vpw64Recomp';      GameId = 'vpw64';           Syms = 'syms\dump.toml';    FrameHook = 'func_80001078' }
    nomercy = @{ Title = 'WWF No Mercy';                Repo = 'NoMercyRecomp';    GameId = 'wwfnomercy';      Syms = 'syms\dump.toml';    FrameHook = 'func_800012B0' }
    vpw2    = @{ Title = 'Virtual Pro Wrestling 2';     Repo = 'Vpw2Recomp';       GameId = 'vpw2';            Syms = 'syms\dump.toml';    FrameHook = 'func_80001440' }
}
