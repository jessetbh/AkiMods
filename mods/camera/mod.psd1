# Build settings for the camera mod, read by ..\..\build.ps1. See ..\cheats\mod.psd1 for the
# field reference.
#
# CAMERA_FUNC builds the camera (view x projection) into CAMERA_MATRIX; found per game as the
# 3-instruction wrapper that calls the 4x4 multiply with a fixed output address (README.md).
# All six games have it; WT / Revenge / VPW64 (1997 engine) were found 2026-10-10 by searching the
# disassembly for the wrapper shape (a2 -> a0, lui a2 <matrix>, jal <float 4x4 multiply>).
@{
    Id      = 'aki_camera'
    Sources = @('src\camera.c')
    Games   = @('wt', 'revenge', 'wm2k', 'vpw64', 'nomercy', 'vpw2')
    Values  = @{}
    PerGame = @{
        wt      = @{ Defines = @('-DCAMERA_FUNC=func_800016F8', '-DCAMERA_MATRIX=0x80042C80u') }
        revenge = @{ Defines = @('-DCAMERA_FUNC=func_800014AC', '-DCAMERA_MATRIX=0x80048288u') }
        vpw64   = @{ Defines = @('-DCAMERA_FUNC=func_800016FC', '-DCAMERA_MATRIX=0x800544F0u') }
        wm2k    = @{ Defines = @('-DCAMERA_FUNC=func_800016A8', '-DCAMERA_MATRIX=0x80057198u') }
        nomercy = @{ Defines = @('-DCAMERA_FUNC=func_80001580', '-DCAMERA_MATRIX=0x80062BA8u') }
        vpw2    = @{ Defines = @('-DCAMERA_FUNC=func_80001768', '-DCAMERA_MATRIX=0x80056F78u') }
    }
}
