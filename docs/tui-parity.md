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

## Parity gaps vs the scrollback UI

- [ ] Local slash commands (`/btw`, `/vim`, `/bookmark`) — slash input is
      rejected with a warning so it never reaches the model.
- [ ] Slash-command Tab completion.
- [ ] Image paste (`Alt+V`) on vision models. (Text paste pills are
      unnecessary here — the input area scrolls internally.)
- [ ] Vim mode.
- [ ] Terminal-native text selection/copy (alternate-screen limitation;
      Textual's own selection has rough edges) — likely answer is a
      "copy last response" binding plus the `$EDITOR` export.
- [ ] Transcript is not left in scrollback on exit — consider printing it on
      quit.
- [ ] The `-r` chat selector runs pre-launch as the raw-terminal Rich UI, not
      as a Textual screen.

## Later / ideas

- Surface web search as a tool line with CC's green/red outcome dots.
- Adopt CC's nested `⎿ Interrupted by user` style for the interrupted marker
  (currently a dim `[interrupted]` line).
