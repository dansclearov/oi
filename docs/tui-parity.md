# TUI parity & design decisions

Tracker for the Textual TUI (`--tui` / `"tui": true` in `config.json`): the
presentation decisions it has made, and where it still trails the scrollback
UI. Update this file when a gap closes or a decision changes.

## Design decisions

- **Claude-Code-style presentation, TUI-only.** The scrollback UI keeps its
  `User:`/`AI:` labels; the TUI uses markers: `❯` user (dim in history, full
  brightness in the prompt), `●` assistant (`ansi_bright_white`), `✱` system
  prompt (dim, shown only when non-empty).
- **Statuses are bare colored text, no marker**: red errors, yellow warnings,
  dim info (billing-switch notices). In CC, dot colors mean tool outcome
  (white = assistant text, green = tool succeeded, red = tool failed); oi has
  no tools besides provider-native web search (currently suppressed in the
  stream), so the dot is assistant-only for now.
- **One dim header line** (`oi · <model> (sub|api) · new chat` /
  `· <title> · <n> messages`) instead of the scrollback banner; keybind hints
  live in a fixed 1-row hint line under the input (`esc to interrupt` while
  streaming, empty when idle).
- **Input pinned at the bottom** with top/bottom border rules only — no side
  borders or padding, so the prompt `❯` column-aligns with the conversation.
- **Short conversations bottom-align** against the input (chat-app gravity;
  Textual's anchored-container behavior). User scroll releases the anchor;
  scrolling back down re-engages it.
- **No vertical scrollbar** (`scrollbar-size-vertical: 0`); wheel and
  PgUp/PgDn still scroll.
- **Blank line between conversation turns only**; the startup block stays
  tight.
- **Thinking stays first-class** grey-italic streamed text — no CC-style
  "Pondering…" filler marker.
- **Slash commands work with a CC-style menu**: typing `/` opens a filtered
  command list above the input (name + description columns); Ctrl+N/P
  navigate, Tab inserts the highlighted command, Esc closes (interrupts the
  stream only when the menu is closed). `/btw` streams its side answer under
  a hollow `○` marker (ephemeral = unsaved); `/bookmark` toggles as in the
  CLI; `/vim` explains vim mode is classic-UI-only.
- **`Alt+V` image paste** on vision models inserts a literal `[Image #N]`
  marker into the input; on submit the markers are spliced back into
  multi-part content. No sentinel pills — plain text markers are enough since
  the input scrolls internally.
- **Vim mode is bespoke** (`tui/vim.py`; prompt_toolkit's `vi_mode` can't be
  embedded, and the only PyPI option runs real vim in a pty). Subset:
  normal/visual/visual-line; `h j k l w b e 0 ^ $ gg G` with counts;
  `f F t T ; ,`; `i a I A o O`; `x X r s S D C ~`; `d c y` with motions,
  doubled, or `i`/`a` text objects (`w`, quotes, bracket pairs); `p P` (one
  internal register); `u`/`Ctrl+R`. No mode indicator by choice. Not
  implemented: `.` repeat, macros, marks, named registers, `>>`/`<<`, `ip`.
  Esc precedence: close slash menu → leave insert/visual → interrupt stream.

## Parity gaps vs the scrollback UI

- [ ] Terminal-native text selection/copy (alternate-screen limitation;
      Textual's own selection has rough edges) — approach TBD, to be
      discussed before building anything.
- [ ] The `-r` chat selector runs pre-launch as the raw-terminal Rich UI, not
      as a Textual screen.

## Decided against

- Printing the transcript to scrollback on exit.

## Later / ideas

- Surface web search as a tool line with CC's green/red outcome dots.
- Adopt CC's nested `⎿ Interrupted by user` style for the interrupted marker
  (currently a dim `[interrupted]` line).
