// aki_modelimport: where a slot's render nodes are (aki-roadmap#161).
//
// No Mercy and VPW2 give every part a render node, contiguous from part 0's. WM2000's root part (0) has none: its
// nodes start at part 1 and are contiguous from there (aki-roadmap#160). So the slot is found by its first part with
// a node, the contiguity check skips parts without one, and a welded pack's matrix segment (part N's node =
// segment + N x stride) is that first node minus its index x stride.
//
// Pure, header-only (host tests: tests/layout_test.c).
#ifndef MI_NODE_LAYOUT_H
#define MI_NODE_LAYOUT_H

// Index of the first part with a node (nonzero), or -1.
static inline int mi_first_node(const unsigned int* nodes, int parts) {
    for (int i = 0; i < parts; i++)
        if (nodes[i]) return i;
    return -1;
}

// Is every part that has a node where the first node's index and the stride put it?
static inline int mi_nodes_contiguous(const unsigned int* nodes, int parts, unsigned int stride) {
    int f = mi_first_node(nodes, parts);
    if (f < 0) return 0;
    for (int i = f + 1; i < parts; i++)
        if (nodes[i] && nodes[i] != nodes[f] + (unsigned int)(i - f) * stride) return 0;
    return 1;
}

// Where part 0's node would be (part N's = this + N x stride): the welded pack's matrix segment base; 0 without nodes.
static inline unsigned int mi_node_base(const unsigned int* nodes, int parts, unsigned int stride) {
    int f = mi_first_node(nodes, parts);
    return f < 0 ? 0 : nodes[f] - (unsigned int)f * stride;
}

#endif
