"""Text fitting helpers shared by stored titles and terminal rendering."""

from rich.cells import cell_len, set_cell_size

ELLIPSIS = "..."
MAX_WORD_SNAP = 20
"""How far back a cut will reach for a word boundary, in cells.

Past this the run being cut isn't a word: scripts without spaces (CJK), URLs
and paths are a single unbroken stretch, and snapping would discard most of
the budget rather than a dangling fragment. Real first messages snap 3 cells
at the median and 20 at the very most.
"""


def truncate_to_cells(text: str, max_cells: int) -> str:
    """Fit ``text`` into ``max_cells`` terminal columns, marking any cut.

    Measures in cells rather than codepoints because wide (CJK) characters
    take two columns each, so a codepoint budget is twice as generous for
    them. A cut is always marked with an ellipsis — the mark means "there was
    more text", whether the text was shortened for storage or for the width of
    the terminal it is being drawn in. Cuts snap back to the last word
    boundary, within ``MAX_WORD_SNAP``, so no dangling partial word is left in
    front of the ellipsis.
    """
    if cell_len(text) <= max_cells:
        return text
    # set_cell_size pads with a space where the cut splits a wide character;
    # stripping that leaves a plain prefix of text.
    cut = set_cell_size(text, max_cells - cell_len(ELLIPSIS)).rstrip()
    if not text[len(cut) :].startswith(" "):
        head, space, fragment = cut.rpartition(" ")
        if space and head and cell_len(fragment) <= MAX_WORD_SNAP:
            cut = head
    return cut + ELLIPSIS
