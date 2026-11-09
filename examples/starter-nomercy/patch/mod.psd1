# Build settings for the starter kit's code change, read by AkiMods build.ps1 (..\build.ps1 calls it with -ModsDir).
# The same fields as every mod in AkiMods\mods (see mods\cheats\mod.psd1):
#   Id       the mod id and .nrm file name: the same as the conversion id (aki.json id and components.patch.mod_id)
#   Sources  C files, relative to this folder
#   Games    the games it builds for (this one is No Mercy only: its addresses are No Mercy's)
@{
    Id      = 'aki_starter_kit'
    Sources = @('src\starter_patch.c')
    Games   = @('nomercy')
    Values  = @{}
}
