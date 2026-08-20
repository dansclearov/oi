from rich.cells import cell_len

from oi.text import truncate_to_cells


def test_text_within_budget_is_returned_unchanged():
    assert truncate_to_cells("short", 20) == "short"
    assert truncate_to_cells("漢字", 20) == "漢字"


def test_wide_characters_are_budgeted_at_two_cells_each():
    """Codepoint budgets are twice as generous to CJK; cells are the truth."""
    fitted = truncate_to_cells("漢字" * 20, 20)
    assert len(fitted) == 11  # 8 wide characters + the ellipsis
    assert cell_len(fitted) <= 20


def test_a_cut_is_marked_with_an_ellipsis():
    assert truncate_to_cells("x" * 30, 10) == "x" * 7 + "..."


def test_a_cut_snaps_back_to_the_last_word_boundary():
    assert truncate_to_cells("alpha beta gamma delta", 20) == "alpha beta gamma..."


def test_a_run_without_spaces_is_cut_where_it_falls():
    """CJK has no word spaces, and a URL is one unbroken run: snapping back to
    the last real space would discard most of the budget, not a fragment."""
    mixed = "bug report " + "日本語のとても長いテキスト" * 3
    assert cell_len(truncate_to_cells(mixed, 60)) >= 58
    url = "look at https://example.com/a/very/long/path/that/keeps/going/for/ages"
    assert cell_len(truncate_to_cells(url, 40)) >= 38


def test_a_cut_that_lands_on_a_boundary_keeps_the_whole_last_word():
    assert truncate_to_cells("alpha beta gamma", 12) == "alpha..."


def test_a_split_wide_character_leaves_no_gap_before_the_ellipsis():
    """A wide character straddling the budget costs a cell rather than a gap."""
    assert truncate_to_cells("漢字漢字漢字", 8) == "漢字..."
