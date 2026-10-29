// aki_modelimport: when may a replaced part be put back? (aki-roadmap#167)
//
// The core saves a part node's vertex block, count and list before it hangs a pack list on it or hides it, and puts
// them back when the slot changes. A slot changes exactly when the game rebuilt the wrestler (new nodes from its node
// pool, the signature hashes them): the old node addresses are then free or already someone else's. VPW2 crashed
// (Tifa vs Snake, after many wrestler changes in character select): a re-used node whose list was 0 (a bone-only part,
// or a part the core hid in the other slot) got the old wrestler's pointers back and drew a freed list.
//
// So a part is put back only when the wrestler's actor still points at the same node, and the node still shows what
// the core left there (nothing, or one of the slot's own lists).
//
// Pure, header-only (host tests: tests/restore_test.c).
#ifndef MI_RESTORE_RULES_H
#define MI_RESTORE_RULES_H

// current_node: the node the part's actor points at now (0 when it has none); saved_node: the node the core saved;
// node_dl: that node's list now; dl0 / dl1: the slot's own lists (both parities).
static inline int mi_restore_ok(unsigned int current_node, unsigned int saved_node, unsigned int node_dl,
                                unsigned int dl0, unsigned int dl1) {
    if (saved_node == 0 || current_node != saved_node) return 0;
    return node_dl == 0 || node_dl == dl0 || node_dl == dl1;
}

#endif
