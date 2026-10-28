# aki_modelimport (aki-roadmap#130): imported models replace wrestlers. Shared core (src\core.c) + one adapter per
# game (src\adapters\<game>.c, picked in src\adapter.c). Needs the game's model host exports (aki_log,
# aki_models_read, aki_models_list): No Mercy / VPW2 feat/130-model-host. Not bundled until the games ship those exports.
@{
    Id      = 'aki_modelimport'
    Sources = @('src\core.c', 'src\adapter.c')
    Games   = @('nomercy', 'vpw2')
    Bundle  = $false
    Values  = @{}
    PerGame = @{
        # WWF No Mercy (USA, Rev 1). These mirror the "runtime" block of AkiTools model-templates/profiles/nomercy.json.
        nomercy = @{
            Defines = @(
                '-DMI_ACTORS=0x80091CC0u',       # part actors: + slot*0x1810 + part*0xDC
                '-DMI_WRESTLER_IDS=0x80076264u', # s16 per slot: the wrestler id (func_800140B4)
                '-DMI_COSTUMES_U8=0x8007626Cu',  # u8 per slot: the costume (func_800140B4's third argument)
                '-DMI_MEM_BASE=0x80400000u',     # packs load here: Expansion RAM No Mercy doesn't use...
                '-DMI_MEM_SIZE=0x003C0000u',     # ...up to 0x807C0000
                '-DMI_SCRATCH=0x807C0000u'       # the core's lists and models.txt (32 KB; below 8 MB so RT64 reads it)
            )
        }
        # Virtual Pro Wrestling 2 (Japan). These mirror AkiTools model-templates/profiles/vpw2.json.
        vpw2 = @{
            Defines = @(
                '-DMI_ACTORS=0x80092900u',       # part actors: + slot*0x157C + part*0xDC (func_80014730)
                '-DMI_WRESTLER_IDS=0x8007F430u', # s16 per slot: the wrestler id (func_80014730)
                '-DMI_COSTUMES=0x80079EBCu',     # s16 per slot: the costume the slot was built with (func_80014730)
                '-DMI_MEM_BASE=0x80400000u',     # packs load here: Expansion RAM no VPW2 code addresses...
                '-DMI_MEM_SIZE=0x003C0000u',     # ...up to 0x807C0000
                '-DMI_SCRATCH=0x807C0000u'       # the core's lists and models.txt (32 KB; below 8 MB so RT64 reads it)
            )
        }
    }
}
