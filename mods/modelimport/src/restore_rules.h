// aki_modelimport: when may a replaced part be put back? (aki-roadmap#167)
//
// The core saves a part node's vertex block, count and list before it hangs a pack list on it or hides it, and puts
// them back when the slot changes. A slot changes exactly when the game rebuilt the wrestler (new nodes from its node
// pool, the signature hashes them): the old node addresses are then free or already someone else's. VPW2 crashed
// (Tifa vs Snake, after many wrestler changes in character select): a re-used node whose list was 0 (a bone-only part,
// or a part the core hid in the other slot) got the old wrestler's pointers back and drew a freed list.
//
// So a part is put back when the node still shows one of the slot's own lists (the game overwrites that list when it
// re-uses a node, so the node is still ours, even if the actor's pointer reads 0 for a moment), or when it shows nothing
// and the wrestler's actor still points at it. A node showing nothing that the actor no longer points at may be anyone's.
//
// Pure, header-only (host tests: tests/restore_test.c).
#ifndef MI_RESTORE_RULES_H
#define MI_RESTORE_RULES_H

// current_node: the node the part's actor points at now (0 when it has none); saved_node: the node the core saved;
// node_dl: that node's list now; dl0 / dl1: the slot's own lists (both parities).
static inline int mi_restore_ok(unsigned int current_node, unsigned int saved_node, unsigned int node_dl,
                                unsigned int dl0, unsigned int dl1) {
    if (saved_node == 0) return 0;
    if (node_dl != 0 && (node_dl == dl0 || node_dl == dl1)) return 1;
    return node_dl == 0 && current_node == saved_node;
}

#endif
