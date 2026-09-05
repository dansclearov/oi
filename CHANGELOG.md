# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html);
while pre-1.0, minor version bumps may include breaking changes (CLI flags,
config/`models.yaml` format, alias names).

## [Unreleased]

### Added

- `astra` alias for OpenAI's GPT-6 Astra (`gpt-6-astra`, Responses API):
  search, thinking, vision, and ChatGPT-subscription billing like `sol`.
- `terra` and `luna` aliases for GPT-5.6 Terra and Luna, same flags as `sol`.

### Changed

- **Breaking:** the `gpt` alias is renamed to `sol` (still `gpt-5.6-sol`).

- The `gemini-flash` alias now points to Gemini 3.8 Flash
  (`gemini-3.8-flash`), replacing 3.7 Flash.

## [0.3.1] - 2026-09-01

### Changed

- The `fable` alias now points to Claude Fable 5.1 (`claude-fable-5-1`),
  replacing Fable 5.

## [0.3.0] - 2026-08-28

### Changed

- The TUI is now the default interface. `--no-tui` runs the classic
  scrollback UI for one run; `"tui": false` in `config.json` makes it the
  default again (`--tui` still overrides that per run).
- `demo.gif` is recorded from a real terminal now (`demo/record.sh` driving
  the TUI with `demo/tui_demo.py`), replacing the VHS tape: VHS's headless
  terminal has no kitty graphics, so it could never show the math rendering.

### Fixed

- The built-in `concise` prompt no longer misspells "concise".

### Added

- TUI: LaTeX math rendering. `$…$`, `\(…\)` inline and `$$…$$`, `\[…\]`,
  `\begin{align}…` display math are typeset (Computer Modern) and shown
  inline as kitty-graphics images sized to the terminal cell, in the terminal's
  foreground color, on the text baseline — in kitty, Ghostty, WezTerm and other
  terminals with the kitty graphics protocol. `align`/`gather`, `pmatrix` &
  friends, `cases` and `array` are laid out on top of matplotlib's mathtext.
  Requires the `latex` extra (`oi-chat[latex]`, i.e. matplotlib); without it,
  or in other terminals, the LaTeX source is shown as before.
  A ```` ```latex ```` / ```` ```tex ```` / ```` ```math ```` code block whose
  content renders is shown as display math too.

## [0.2.3] - 2026-08-27

### Added

- TUI: conversation branching. `↑` on an empty prompt walks back through your
  messages (`↓` returns to the prompt); `Enter` edits one in place and
  sending it starts a new branch from that point, while the old branch is kept
  and `←`/`→` on that message switch between them (a dim `‹ 2/3 ›` marks
  messages with alternatives). Vim mode adds `h`/`j`/`k`/`l`. Dormant branches
  are stored in `branches.json` next to `messages.json`, which stays the
  active branch, so everything else (`-c`, search, stats, older versions) sees
  the conversation as before.

### Changed

- TUI: reopening a chat replays thinking traces and native tool lines (web
  search / fetch / code) in their original order, not just the answer text.
- TUI: a conversation shorter than the screen starts at the top and grows
  downward instead of hugging the input.
- Interrupting a streaming reply (`Ctrl+C` / `Esc`) now keeps the exchange in
  the chat, Claude-Code-style: the partial answer stays in context, so a
  follow-up can steer the model instead of starting the turn over. Interrupting
  *before* any output arrives unsends instead — the message returns to the
  input for review, for catching a mistake right after an autopiloted Enter.
  Previously both cases discarded the whole exchange. Both frontends.

### Fixed

- TUI vim mode: `Backspace` and `Delete` in normal/visual mode no longer fall
  through to the editor and delete text; they act as `h` and `x`.

## [0.2.2] - 2026-08-23

### Added

- The TUI now shows server-side tool activity while a search-enabled model
  works: one line per call — `Web Search("query")`, `Fetch(url)`,
  `Code(...)` — updated in place as the query streams in and the results
  land, so long search turns are no longer a silent wait.

- Mac-friendly key chords, for terminals that forward them: `Ctrl+V` pastes
  an image from the clipboard (both frontends — Mac terminals keep their own
  paste on `Cmd+V`, and their Option key doesn't send `Alt+V` by default),
  and `Cmd+C` copies the selection in the TUI without ever arming the
  double-press exit that `Ctrl+C` carries.

### Changed

- Startup is ~4x faster (~600ms less before the UI appears, both frontends):
  pydantic-ai — the bulk of oi's import cost — is now loaded in the
  background after the UI is up instead of before anything paints.

- Bookmarked chats are marked with `*` in the chat selector (`oi -r`) instead
  of a star: `★` is an East-Asian-ambiguous-width character, so terminals
  disagree on whether it takes one cell or two.
- A shortened chat title now ends in `...`, whether it was shortened to fit
  the title cap or to fit the terminal — the mark means "there was more text"
  either way. Titles cut from a first message used to be shortened silently.
  Cuts snap back to the last word boundary, where the script has one.
  `oi stats` marks a trimmed chat title the same way.

### Fixed

- Headings in a cited answer no longer swallow the paragraph that follows
  them (`## SpecsBoth models have a 1M token context window...`, rendered as
  one long heading). Anthropic splits a search-backed answer into one text
  block per citation and strips each block's trailing newlines, so the break
  after a heading was lost when the next citation started the paragraph.
- Chat titles are capped in terminal cells rather than codepoints, so titles
  in CJK and other wide scripts are no longer stored at twice their budget —
  which is why they, and only they, showed up truncated in the chat selector
  (`oi -r`).
- TUI: markdown tables are sized to their content instead of being stretched
  to the full width of the pane.
- Chat selector (`oi -r`): titles with wide characters (CJK, and any
  other double-width script) no longer overflow the row and wrap — the
  title column is measured and padded in terminal cells now.
- TUI: adding a line to the input no longer jolts the conversation — it used
  to settle a row short and jump one frame later, on every newline.
- TUI: once the input grows past its height cap, the text no longer re-wraps
  (a scrollbar was appearing and narrowing it) and the cursor no longer trails
  the line being typed by a frame.
- TUI: deleting a newline sends the cursor straight to the end of the line
  above, instead of flicking up a row and back.
- TUI: a vim `dd` that shrinks the input resizes it in the same frame, rather
  than leaving it a row too tall until the next one.

## [0.2.1] - 2026-08-14

### Added

- `/tui` toggles full-screen TUI mode and persists it to `config.json`, so it
  no longer needs a hand-edited config or `--tui` on every run. Available from
  both frontends; applies on the next launch.
- `--no-tui` runs the classic scrollback UI for one run, overriding
  `"tui": true` in `config.json`.
- The TUI header shows `· search` when search is on and the model supports
  it, so an active web-search session is visible at a glance.
- `/search` turns web search on mid-chat, so noticing you need it no longer
  means quitting and relaunching with `oi -c --search`. There is no off switch.

### Changed

- TUI: pasted-image markers are styled as cyan pills, matching the scrollback
  UI — while composing, in the sent message, and when replaying a chat.
- The `gemini-flash` alias now points to Gemini 3.7 Flash
  (`gemini-3.7-flash`), replacing 3.6 Flash.

### Fixed

- TUI: sending a message no longer stutters. Pressing Enter waited on building
  the model for the turn — a few hundred ms on the first message of a run,
  while the provider SDK is imported — and then took three screen repaints,
  each a full relayout of the conversation, so the input emptied and the
  message appeared separately. The message is now echoed immediately and in a
  single repaint.
- Models are built off the event loop and reused across turns, so the UI stays
  responsive while a turn starts and requests reuse a warm connection.

## [0.2.0] - 2026-08-09

### Added

- Full-screen TUI mode (built on Textual) that renders responses as
  live-streamed markdown — tables, syntax-highlighted code fences, lists —
  in the terminal's own ANSI palette, with a Claude-Code-style presentation:
  marker glyphs instead of role labels, a dim one-line session header, and a
  bordered input pinned at the bottom with a contextual hint line. Opt in per
  run with `--tui` or by default with `"tui": true` in `config.json`; the
  classic scrollback UI remains the default. Local slash commands work with a
  completion menu (type `/`; Ctrl+N/P to navigate, Tab to complete), `Alt+V`
  image paste is supported on vision models, and vim mode has its own modal
  emulation (normal/visual/visual-line, counts, `d c y` operators with
  motions and text objects, `f/t` finds, paste register, undo/redo). The
  input cursor is the terminal's own — steady, never blinking, bar-shaped in
  insert and block in normal/visual. Ctrl+C interrupts a running response,
  copies the current selection when there is one, and otherwise asks for a
  second press to exit. A status line under the input shows the vim mode
  (`-- INSERT --`, `-- VISUAL --`) alongside contextual hints.

### Changed

- The `gemini-flash` alias now points to Gemini 3.6 Flash (`gemini-3.6-flash`),
  replacing Gemini 3.5 Flash.
- `--search` now also gives the model a web-fetch tool, so it can read a URL you
  paste instead of only searching for it. Applies to Anthropic and Gemini
  models; OpenAI and xAI already open pages from within their search tool.
- Require `pydantic-ai>=2.24.0` (was `>=2.9.0`). It carries the fix for
  GHSA-v2xh-2vp8-57h8, recognizes Claude Opus 5 — so the default `opus` alias
  gets the newer web-search tool with dynamic filtering instead of the basic
  variant — and handles Anthropic's `stop_reason=pause_turn`, which a
  search-plus-fetch turn can hit.

### Fixed

- Interrupting or failing the first turn of a new chat no longer loses the
  system prompt: discarding the pending user message now restores the prompt,
  so the next message carries it again.
- `config.json` is now written atomically, so a crash or a second `oi` session
  reading mid-write can no longer leave settings (`vim_mode`, `tui`,
  `default_prompt`) silently reverted to their defaults. A config that can't
  be written (read-only home, full disk) is now reported instead of swallowed:
  `/vim` says the toggle applies to the session only.

## [0.1.6] - 2026-07-24

### Added

- `oi docs models`: LLM-agent-oriented documentation for adding models to the
  user `models.yaml` — config shape, pydantic-ai 2.x naming traps, API key
  handling (keys should never pass through the agent conversation), a
  verification command, and the built-in default config (the merge base), with
  user paths resolved into the text. `oi --help` now points to it via a new
  epilog, so coding agents discover it on their own.
- Hardened `oi docs models` after agent field-testing: it now spells out that
  the per-model schema and the provider set are closed (unknown YAML keys are
  silently ignored; providers not shipped by pydantic-ai can't be added via
  config), links pydantic-ai's raw-Markdown docs as the authoritative provider
  reference, and reports which `*_API_KEY` env vars are set — names only — so
  agents never need to read the secrets file.

### Changed

- The `opus` alias now points to Claude Opus 5 (`claude-opus-5`), replacing
  Claude Opus 4.8.
- Upgraded to Pydantic AI 2.x (`pydantic-ai[groq,xai]>=2.9.0,<3.0`). oi's own
  code needed no changes — it builds on the `direct` API, so v2's rewrite of the
  `Agent` / harness layer doesn't touch it. The `groq` and `xai` extras are now
  requested explicitly because 2.x stopped bundling provider extras by default,
  which would otherwise have broken both providers.

### Fixed

- **If you have a custom `~/.config/oi/models.yaml`, check your provider
  prefixes.** Two Pydantic AI 2.x renames affect configs that followed the
  commented examples in `models_template.yaml`, which have been corrected:
  - The bare `openai:` prefix now routes to the **Responses API**; it used to
    mean Chat Completions. This change is silent — models keep working, but hit
    a different API surface. Non-reasoning models that want the old behaviour
    belong under `openai-chat:`.
  - The xAI prefix is now `xai:`; the `grok:` alias was removed.

  oi's built-in models are unaffected — they already use `openai-responses`,
  `anthropic`, and `google`.
- Refreshed the commented examples in `models_template.yaml`, which had drifted
  far enough that four of the model IDs no longer existed at all
  (`gpt-4.5-preview`, `chatgpt-4o-latest`, `grok-4.1`, `kimi-k2-thinking`, plus
  OpenRouter's retired `deepseek-r1-0528:free`). They now point at current
  models, and the footer notes which providers need an opt-in Pydantic AI extra.

## [0.1.5] - 2026-07-12

### Changed

- The `gpt` alias now points to OpenAI's GPT-5.6 Sol
  (`openai-responses/gpt-5.6-sol`), replacing GPT-5.5. Sol is the flagship tier
  of the new GPT-5.6 family and remains subscription-eligible.

## [0.1.4] - 2026-07-07

### Added

- oi-scoped API keys: `~/.config/oi/env` (dotenv format) is loaded at startup
  and overrides inherited environment variables, so oi's billing is isolated
  from shell/global keys. Keys not set in the file still fall back to the
  environment.

## [0.1.3] - 2026-07-03

### Added

- Claude Fable 5 (`anthropic/claude-fable-5`) and the `fable` alias. Anthropic's
  most capable widely released model (1M context, 128K output, vision, adaptive
  thinking). High-risk cyber/bio/chem prompts are blocked server-side.

### Changed

- The `sonnet` alias now points to Claude Sonnet 5 (`claude-sonnet-5`),
  replacing Claude Sonnet 4.6.

- The interactive chat selector (`oi -r`) now adapts to the terminal size: the
  number of rows shown fits the height, and rows, the header, and the help line
  reflow to the width instead of wrapping. This makes it usable on narrow
  screens, e.g. a phone over SSH.

### Fixed

- Multi-line pastes in terminals that send `\r` line endings in bracketed
  paste (e.g. iTerm2) no longer render garbled in the input buffer, and their
  lines now count toward the paste-pill threshold.

## [0.1.2] - 2026-06-04

### Added

- `/btw <question>` in-chat command for one-off side questions. The model
  answers with the full conversation as context, but neither the question nor
  the answer is appended to the chat or saved. Search and thinking follow the
  session's settings; the reply renders under an `AI (btw):` label.

### Changed

- `--help` output is tidier: a short two-line usage that leads with the common
  flags, options split into labeled groups (chat selection / model & prompt /
  headless / behavior), and model/prompt choices moved out of the usage line
  into their help text. `oi auth -h` now shows the available actions
  (login/logout/status) directly instead of requiring a second `oi auth openai
  -h`, and the subcommand usage lines no longer inherit a mangled prog.
- The built-in default system prompt is now `empty` (no system prompt) instead
  of `general`. Modern models already cover what `general` steered, and a blank
  prompt better reflects the raw model. Set `default_prompt` in
  `~/.config/oi/config.json` or pass `-P` to override. The `general` prompt also
  dropped its stale "don't use the online tool" line.
- Smart titles are now generated with a fixed cheap model (`haiku`) instead of
  the chat's active model, so naming a chat never costs an expensive turn. When
  `ANTHROPIC_API_KEY` isn't configured, smart titling is skipped and the chat
  keeps its first-message title.

### Fixed

- Interrupting a streaming reply with Ctrl+C no longer leaves blank lines before
  the next prompt. The renderer was emitting its end-of-response newlines on top
  of the chat loop's own, so the next `User:` now sits directly below the broken
  stream (previously one blank line during output, two during thinking traces).
- Anthropic models with a pinned thinking budget (Haiku) no longer think when
  thinking is disabled. Their `extra_params` budget was being merged
  unconditionally, so `--no-thinking` turns and the smart-title call still paid
  for reasoning tokens; thinking is now explicitly disabled when off.

## [0.1.1] - 2026-06-03

### Added

- Search, preview, and editor view in the `oi -r` chat selector. Press `/` to
  filter chats by title or message text; Enter applies the filter and hands the
  navigation keys back, Esc clears it. Tab toggles a preview pane showing the
  conversation (`Ctrl+P`/`Ctrl+N` scroll, `gg`/`G` jump to top/bottom). Press
  `e` to open the highlighted chat in `$EDITOR` as a clean Markdown transcript.
- ChatGPT subscription billing for OpenAI models. `oi auth openai login` signs
  in with a ChatGPT Pro/Plus/Team plan; once logged in, Codex-eligible OpenAI
  models route through the subscription automatically (no API key needed and no
  config change), falling back to your API key for everything else. When the
  subscription's usage limit is reached, oi transparently switches that chat to
  your API key and returns to the subscription once it resets. Set
  `OI_NO_SUBSCRIPTION=1` to always use the API key. Manage sign-in with
  `oi auth openai [login|logout|status]`.
- `oi stats` — usage statistics across all chats: totals, per-model breakdown,
  activity streaks, busiest hour/day, and a GitHub-style activity heatmap.
  Add `--deep` to scan transcripts for word counts (yours vs the AI's), AI
  reading time, your wordiest chat, images, and thinking usage.

### Changed

- Resuming a chat now always prints a single "Continuing chat: …" banner,
  regardless of how it was loaded (`-c`, `-r ID`, or the `-r` selector) or how
  many messages it has. Previously `-r ID` also printed a redundant "Loaded
  chat:" line, and short chats printed nothing at all.
- `opus` alias now points to `claude-opus-4-8` (was `claude-opus-4-7`).
- Require `pydantic-ai>=1.104.0`, the first release that recognizes
  `claude-opus-4-8`. Earlier versions gave it a fallback profile with no
  adaptive thinking or effort support, so thinking traces came back empty.

### Fixed

- Resuming a chat (`oi -r`/`-c`) no longer prints a spurious "locked to its
  original model" notice when you didn't pass `--model`. The notice now appears
  only when you explicitly request a different model than the chat was created
  with.
- Exiting a chat with Ctrl+C now re-saves it, bumping its `updated_at` so
  `oi -c` reopens the chat you just closed — even if you only re-read it
  without sending a new message. (Skipped in `--ephemeral`.)

## [0.1.0] - 2026-05-24

Initial public release.

### Changed

- Rebranded from `llm-cli` to `oi`: command, Python package, config/data
  directories, and PyPI distribution name (`oi-chat`).
- Upgraded `pydantic-ai` to 1.100 and migrated from `builtin_tools` to the
  `native_tools` API.
- Use the non-deprecated `google` provider prefix (was `google-gla`).

### Added

- PyPI packaging metadata (license, classifiers, project URLs).
- Support for Python 3.10–3.13.

[Unreleased]: https://github.com/dansclearov/oi/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/dansclearov/oi/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/dansclearov/oi/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/dansclearov/oi/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/dansclearov/oi/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/dansclearov/oi/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/dansclearov/oi/compare/v0.1.6...v0.2.0
[0.1.6]: https://github.com/dansclearov/oi/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/dansclearov/oi/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/dansclearov/oi/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/dansclearov/oi/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/dansclearov/oi/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/dansclearov/oi/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dansclearov/oi/releases/tag/v0.1.0
