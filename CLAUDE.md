# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) style:

```
<type>(<optional scope>): <description>
```

Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

Examples:
- `feat(models): add support for Gemini 2.0`
- `fix(client): handle retry on rate limit errors`
- `refactor: extract ChatSelector into ui module`

## Development Commands

**Testing:**
```bash
uv run pytest                    # Run all tests
uv run pytest tests/test_main.py # Run specific test file
```

**Code Quality:**
```bash
uv run ty check           # Type checking (ruff formatting/linting handled by pre-commit)
```

**Installation & Setup:**
```bash
# Local development with uv
uv install

# Install dev dependencies
uv install --group dev

# Set up pre-commit hooks
uv run pre-commit install

# Add new dependencies
uv add <package-name>     # Add a new dependency

# Global installation
pipx install -e .         # Install from local copy
pipx install --force -e . # Reinstall after changes
```

**Running the application:**
```bash
uv run oi                      # CLI interface with default settings
uv run oi -P concise -m sonnet # Use specific prompt and model
```

## Packaging & Releases

**Names:** the PyPI distribution is **`oi-chat`**, but the command and import
package are **`oi`** (`[project.scripts] oi = "oi.main:main"`). Both `oi` and
`oi-cli` are unavailable on PyPI (`oi` is an abandoned package; `oi-cli` is
rejected as too similar to the existing `oicli`). Because the dist name no longer
matches the package dir, hatchling needs the explicit
`[tool.hatch.build.targets.wheel] packages = ["src/oi"]` in `pyproject.toml`.

**Python support:** `requires-python = ">=3.10"`. CI (`.github/workflows/ci.yml`)
runs the test suite across 3.10–3.13 plus a lint job (`pre-commit run
--all-files` + `ty check`) on every push/PR.

**Action versions:** the `actions/*` steps use floating major tags (e.g.
`actions/checkout@v6`). `astral-sh/setup-uv` is the exception — it's pinned to a
full commit SHA (`@<sha> # v8.1.0`) because it doesn't publish a floating major
tag past `v7` (so `@v8` 404s), and SHA pinning is setup-uv's own recommended
approach. Bump the SHA + comment together when updating.

**Publishing is automated** via `.github/workflows/release.yml`, triggered on
`v*` tags. It (1) builds the sdist + wheel, (2) publishes to PyPI via **Trusted
Publishing (OIDC)** — no API tokens; relies on a PyPI publisher + a GitHub `pypi`
environment that are already configured, and (3) creates a GitHub Release whose
notes are the matching `CHANGELOG.md` section, with the build artifacts attached.

**To cut a release:**
1. Bump `version` in `pyproject.toml`.
2. In `CHANGELOG.md`, move the `[Unreleased]` entries under a new
   `## [x.y.z] - <date>` heading (add a fresh empty `[Unreleased]`; update the
   link refs at the bottom).
3. Run `uv lock` — `uv.lock` pins `oi-chat`'s own version, so it goes stale on
   every bump. Commit it with the release; otherwise it drifts a version behind.
4. Commit and push `main`.
5. `git tag vX.Y.Z && git push origin vX.Y.Z` — the workflow does the rest.

Keep notable changes under `CHANGELOG.md`'s `[Unreleased]` as you go: the release
job extracts the per-version section, so it must exist before tagging. Pre-1.0,
bump the **minor** for breaking changes (CLI flags, config/`models.yaml` format,
alias names) and the **patch** for features and fixes.

## Architecture Overview (Post-Refactoring)

**Directory Structure:**
```
src/oi/
├── core/              # Core business logic
│   ├── client.py      # LLMClient - API calls & retry logic
│   ├── codex_auth.py  # ChatGPT subscription (Codex OAuth) login, token store, rate-limit telemetry
│   ├── session.py     # Chat & ChatMetadata - data models + Chat.create_new()
│   ├── chat_manager.py # ChatManager - CRUD operations
│   ├── chat_repository.py # ChatRepository - filesystem persistence
│   ├── message_utils.py # Message serialization & history helpers
│   ├── smart_title.py # Smart title generation
│   └── stats.py       # StatsCollector - aggregate stats over chat history
├── config/            # Configuration management
│   ├── settings.py    # Config class + user config (JSON) management
│   └── loaders.py     # YAML model configuration loading & merging
├── ui/                # User interface components
│   ├── input_handler.py # InputHandler - prompt_toolkit integration
│   ├── chat_selector.py # ChatSelector - interactive chat picker
│   ├── image_paste.py # PasteStore (images + long text) + PillProcessor + clipboard image reader
│   ├── labels.py      # Shared ANSI/Rich/prompt-toolkit label styling
│   ├── transcript.py  # Shared plaintext/styled/search views of a chat (selector)
│   └── stats_view.py  # Rich rendering for `oi stats` (heatmap, bars)
├── tui/               # Full-screen TUI frontend (Textual), `tui` config knob
│   ├── app.py         # OiApp - chat screen, turn worker, message handlers
│   └── renderer.py    # TuiRenderer - ResponseRenderer → Textual messages
├── llm_types.py       # Shared chat/model capability dataclasses
├── app.py             # Main application orchestration + ChatLoopContext
├── cli.py             # Command-line argument parsing
├── main.py            # Entry point (delegates to app.py)
├── constants.py       # All constants & UI config
├── exceptions.py      # Custom exception classes
├── local_commands.py  # Local in-chat slash command registry + completion
├── prompts.py         # Prompt file loading
├── registry.py        # ModelRegistry - alias + capability management (single config load)
├── text.py            # Cell-accurate text fitting (titles, terminal rows)
└── renderers.py       # Response rendering (StyledRenderer)
```

**Multi-provider LLM Client:**
Supports OpenAI, Anthropic, DeepSeek, Google Gemini, xAI, and OpenRouter through Pydantic AI's `direct` APIs with a unified interface.

**Centralized Model Registry:**
- `ModelRegistry` loads merged config once via `load_merged_model_config()`, then derives both the model map and capabilities from it
- Providers are "dumb" API clients - no hardcoded model definitions
- Default model configurable via `aliases.default` in YAML
- Cross-provider aliases supported

**Model Configuration:**
- **Minimal default config**: `src/oi/models.yaml` contains only latest SOTA models with date-free aliases
- **Auto-generated user config**: `~/.config/oi/models.yaml` created on first run from `models_template.yaml`
- **Deep merge**: User config merges with defaults at model property level (can add just `extra_params` without repeating all capabilities)
- **YAML anchors**: Top-level keys starting with `_` are ignored (prevents anchors from being treated as providers)
- **extra_params support**: Model-specific settings (OpenRouter quantization, OpenAI `openai_reasoning_effort`, etc.) merged into `model_settings` before API calls
- Per-model settings: `max_tokens`, `supports_search`, `supports_thinking`, `supports_vision`, `extra_params`

**Configuration & Prompts:**
Dual-location system:
1. User config directory (`~/.config/oi/prompts/`) - takes precedence
2. Package built-in prompts (`src/oi/prompts/`)

Format: `prompt_[name].txt`, loaded via `prompts.py:read_system_message_from_file()`

**Chat Management:**
- Rich-based interactive chat selection via `ui/chat_selector.py`
- Automatic session persistence with metadata in `core/session.py`
- Smart title generation (triggers after 8+ messages)
- Auto-save functionality

**Title Length (`text.py:truncate_to_cells`):**
- `MAX_TITLE_LENGTH` is a budget in **terminal cells**, not codepoints — one
  CJK character costs two — and it is what both title paths
  (`_update_title_from_first_user_message`, `SmartTitleGenerator._sanitize_title`)
  and the chat selector's title column measure against. Counting codepoints
  stored a wide-script title at twice the selector's column width, so those
  titles, and only those, came back ellipsised.
- Every cut is marked with `text.py:ELLIPSIS` (three ASCII dots), at whichever
  layer made it — the chat selector's rows, `oi stats`' biggest-chat title, and
  the stored titles themselves. The mark means
  "there was more text", not "your terminal is narrow". Since titles are stored
  within the cap and the selector's column is slightly wider (78), a wide
  terminal shows stored titles verbatim and only narrow ones re-cut.
- The word-boundary snap is bounded by `MAX_WORD_SNAP`: a run without spaces
  (CJK, a URL, a path) is one unbroken "word", so an unbounded snap would
  discard the whole budget instead of a dangling fragment.

**Smart Titles (`core/smart_title.py`, gated in `app.py:_maybe_generate_smart_title`):**
- Titles are generated with one fixed cheap model — `SMART_TITLE_MODEL` (`haiku`)
  — **not** the chat's active model, so a title never costs an Opus/GPT-5 turn.
- The model and the env var it needs are coupled in `constants.py`
  (`SMART_TITLE_MODEL` + `SMART_TITLE_API_KEY_ENV = "ANTHROPIC_API_KEY"`). When
  that key isn't set, smart titling is skipped entirely and the chat keeps its
  first-message title (`_update_title_from_first_user_message`). The skip leaves
  `smart_title_generated` unset, so a title is generated later if the key appears.

**Chat Selector Search / Preview / Editor (`ui/chat_selector.py`):**
- Hand-rolled raw-key loop driving a Rich `Live` (NOT prompt_toolkit), so live
  text entry (search), modes, and scroll are all handled manually.
- `ui/transcript.py` is the shared formatter (built on `flatten_history`): the
  same role+text view feeds the search blob (lowercased title+body), the
  `$EDITOR` export (`**User:**`/`**AI:**` Markdown, `.md` temp file), and the
  preview pane (Rich `Text` with `ui/labels.py` styling).
- **Search** is modal: `/` enters typing mode and live-filters on a substring of
  title+body; Enter *applies* (keeps the filter, returns the normal navigation
  keybinds), Esc clears it (also clears a committed filter from normal mode). The
  search index + `chat_cache` (id→`Chat`, `None` for unreadable) are built lazily
  on first `/` so transcripts load once.
- **Preview** is a full-width bottom pane toggled with Tab (side-by-side was
  dropped — bottom shows both panes at once with more room). Height is
  `clamp(term_h − list − chrome, PREVIEW_MIN_HEIGHT, PREVIEW_MAX_HEIGHT)`;
  content is windowed via `console.render_lines` + a scroll offset that resets
  on selection change. `Ctrl+P`/`Ctrl+N` scroll (fall back to list nav when the
  pane is closed), `gg`/`G` jump to top/bottom.
- **`e`** opens the highlighted chat in `$EDITOR` read-only. The stop/start dance
  needs `console.control(Control.move(0, -1))` after `live.stop()`: `stop()`
  emits a trailing newline, leaving the cursor one row below the frame, so the
  next refresh's upward erase misses the top line and duplicates the header —
  stepping up one row realigns it (do NOT use `transient=True`; it rewinds over
  scrollback and corrupts it on tall terminals).
- `_read_key_unix` peeks with `select()` after `\x1b` so a lone Esc doesn't block
  waiting for the rest of an escape sequence (needed for Esc-to-clear).

**Headless Mode:**
- `-p MESSAGE` sends one turn and exits; composes with `-c` / `-r ID` to follow up against existing chats (appends in-place, same chat ID)
- `--ephemeral` skips all persistence. Combined with `-c` / `-r` it runs a scratch turn against the existing chat's context without modifying it. Works in interactive mode too — the save gate is in `run_chat_loop` via `ctx.ephemeral`
- `run_headless_turn()` in `app.py` is the headless entry point; `main()` branches to it when `args.prompt is not None`
- Output cleanups for pipe-friendliness: `AI:` label hidden via `ChatOptions.show_assistant_label=False`, `Loaded chat:` / "No previous chats" chatter suppressed via `handle_chat_selection(quiet=True)`. Thinking traces still render unless `--hide-thinking` is passed (compose them for clean stdout)
- `-r` without an ID errors in headless — interactive selector is unavailable

**Stats Subcommand (`oi stats`):**
- `cli.py` adds an optional `add_subparsers(dest="command")`; `command` is `None` for the normal chat path. `main()` branches to `run_stats()` when it's `"stats"`.
- `StatsCollector.collect()` (`core/stats.py`) does a cheap metadata-only pass; `--deep` also loads each transcript to count user/AI words (and the wordiest chat). Words come from text parts, so they exclude thinking traces and search results — token counts are intentionally not reported (input is cumulative, output is dominated by reasoning).
- Keep `core/stats.py` Rich-free; rendering lives in `ui/stats_view.py`.

**LLM-First Docs (`oi docs`):**
- `oi docs [topic]` (only topic: `models`, the default) prints a Markdown guide
  to stdout, written primarily for coding agents (Claude Code etc.) editing the
  user's `models.yaml` on request. The `--help` epilog advertises it — that's
  the agents' discovery hook; keep the epilog short and put payload in the doc.
- Content lives in `src/oi/docs/<topic>.md` (ships in the wheel like `prompts/`),
  rendered by `app.py:run_docs()` via `string.Template.safe_substitute` — NOT
  `str.format`, because the YAML examples contain literal `{}`. Substitutions
  make the doc self-contained (fewer agent round trips): resolved user
  `models.yaml` path + whether it exists, env-file path, installed pydantic-ai
  version, which `*_API_KEY` env vars are set (names only — so agents never
  read the secrets file to check), and the verbatim built-in `models.yaml` as
  the merge base (read at print time so it can't drift from the shipped
  defaults).
- The doc states the closed worlds explicitly (per Opus field-testing): the
  six model keys are the whole schema and unknown keys are silently ignored
  (so agents don't invent `base_url:` and report false success), and the
  provider set is fixed by pydantic-ai — with a static prefix list labeled by
  version and links to pydantic-ai's raw-Markdown docs
  (`https://pydantic.dev/docs/ai/models/<provider>/index.md`, `llms.txt`) as
  the authoritative source. A live provider list was considered and rejected:
  pydantic-ai's registry is an if/elif chain, so extraction would mean
  regexing its source.
- The doc deliberately tells agents to have the user add API keys themselves
  (after the config work is done) instead of asking for the key
  in-conversation, so keys don't end up in logs or provider training data.
- New pydantic-ai gotchas (prefix renames, extras) belong in the doc's
  "naming traps" section too, not just in this file and `models_template.yaml`.

**ChatGPT Subscription Billing (OpenAI, `core/codex_auth.py`):**
- `oi auth openai [login|logout|status]` (login is the default; bare `oi auth` prints status). `cli.py` nests provider→action subparsers; `app.py:run_auth()` dispatches. Login is a browser PKCE OAuth flow (reusing Codex's `client_id`) with a `localhost:1455` loopback; tokens are stored at `~/.config/oi/auth/openai.json` (mode `0600`) and auto-refreshed near expiry.
- **Seamless routing**: when logged in, a model with `supports_subscription: true` (only on `openai-responses` gpt-5.x) bills to the ChatGPT subscription instead of the API key — no `models.yaml` change, no separate provider/alias. `client.py` builds an `OpenAIResponsesModel` **instance** pointed at the Codex backend (`CODEX_BASE_URL = https://chatgpt.com/backend-api/codex`) with a token-injecting httpx client, and passes that instance (not the `provider:model` string) to `model_request_stream`; everything else stays on the API key. Eligibility is the capability flag, gated by `_use_subscription()` / surfaced by `subscription_billing_active()`. `OI_NO_SUBSCRIPTION=1` forces the API key.
- **Codex endpoint quirks** (all handled, no spoof string needed): requests must carry `Authorization: Bearer` + `ChatGPT-Account-Id` headers, set `openai_store=False`, and include a (possibly empty) `instructions` key — pydantic-ai omits empty instructions, so `_InstructionsTransport` re-adds `instructions: ""` to preserve an empty system prompt.
- **Exhaustion → auto-fallback + auto-revert**: the Codex backend returns `x-codex-{primary,secondary}-used-percent` / `-reset-at` headers on every response (primary ~5h, secondary ~7d window; exhausted == used-percent 100). A `response` hook (`record_rate_limit_headers`) snapshots them; `is_exhausted()` is true while a window is maxed before its `reset_at`. While exhausted, routing uses the API key; if a turn hits the limit mid-request, `_stream_with_fallback` retries that turn on the API key in-place (gated on `is_exhausted()` set by the hook — never error-body guessing). After `reset_at` it auto-returns to the subscription; both transitions print a one-line notice.
- **Billing indicator**: `app._billing_tag()` shows ` (sub)`/` (api)` on the chat-start banner — `(sub)` only when actually billing to the subscription, `(api)` otherwise (including non-subscription providers).

**TUI Mode (`src/oi/tui/`, off by default):**
- Enabled per run with `--tui` or persistently via `"tui": true` in
  `config.json` (`Config.tui`) — the `/tui` slash command writes that key from
  either frontend, so it applies on the next launch, not mid-session (unlike
  `/vim`, which also takes effect immediately). `--tui`/`--no-tui` are paired
  flags over one dest defaulting to `None`, so "unset" stays distinguishable
  from "off" and only then does `config.json` decide. `main()` branches to
  `tui.app:run_tui` for the interactive chat path only — headless (`-p`), `stats`, `docs`, `auth`, and
  the pre-launch chat selector are unchanged. The import is deferred so the
  scrollback path never pays for textual.
- The win over scrollback: assistant text renders as **live-streamed markdown**
  via Textual's `Markdown.get_stream()` (`MarkdownStream` buffers tokens and
  re-parses only the final open block). Streaming re-render is impossible in
  scrollback (Rich `Live` clobbers content taller than the screen), which is
  why the classic UI streams plain text.
- **Turn flow**: `ChatInput` (TextArea subclass; Enter submits,
  Shift+Enter/Ctrl+J newline) → async worker runs `LLMClient.chat_async` with
  a `TuiRenderer` injected via `renderer_factory`. The renderer's sync hooks
  post Textual messages (`TextDelta`, `ThinkingDelta`, …); the app's async
  handlers mount widgets and feed the stream — the FIFO message queue
  preserves delta order. The worker posts `TurnFinished` after `chat_async`
  returns (all paths), which stops the stream; on cancellation it discards the
  pending user message first (Ctrl+C parity with the CLI loop). The worker is
  started from `call_after_refresh`, not inline: `chat_async`'s first step is
  synchronous and can block the loop for a while (see `_resolve_model`), which
  before the paint means the message looks stuck in the input. Re-entrancy is
  guarded on `_turn_active` (set synchronously at submit) rather than on
  `_turn_worker`, which only exists a frame later.
- **Flicker discipline**: the submit path clears the input and mounts the
  echoed row inside one `batch_update`, awaiting the mount — mounting costs a
  refresh cycle of its own, so an unbatched submit paints the emptied input
  one relayout before the message shows up. `ChatInput.sync_height` writes
  `styles.height` only when the value changes because height is a
  layout-invalidating property (a write per keystroke relayouts the screen).
  Textual's layout pass (`Screen._refresh_layout` → `Compositor.reflow`) walks
  *every* widget in the tree, not just visible ones, so its cost scales with
  the whole conversation (~15ms at 10 turns, ~70ms at 100) and each avoidable
  relayout is felt: hint updates pass `layout=False` for that reason, and
  `tests/unit/tui/test_app.py` asserts the submit is one paint by sampling
  `Compositor.visible_widgets` per frame (`mount()` registers a widget
  immediately, so a DOM query can't tell a laid-out row from a pending one).
  A height change also costs *two* layout passes — the compositor writes each
  resized widget's `virtual_size`, a `layout=True` reactive, so the resize
  dirties layout again — and the pass that shrinks the log re-anchors it from
  the container height recorded on the previous pass. Left alone the
  conversation therefore lands a row short and jumps on the follow-up pass, on
  every newline: `ChatLog.preempt_resize` (called from `sync_height`) records
  the height the pane is about to have so the first pass anchors it right,
  with the follow-up pass still there to correct a wrong guess. That call is
  why `ChatInput` needs a `ChatLog` sibling on the screen — the vim tests'
  harness composes one for that reason.
- **The height sync is synchronous with the edit**: `sync_height` runs from
  `ChatInput.edit` (plus `undo`/`redo`, which bypass `edit`), *not* off the
  `Changed` message the edit posts — a frame can be painted before that
  message is handled, which showed up as the input keeping its old height for
  a frame after a vim `dd`. The app's `TextArea.Changed` handler still calls
  it, harmlessly: by then it's a no-op.
- **The caret and a pending resize**: `TextArea` points the terminal cursor at
  `cursor_screen_offset`, measured against the geometry the input has *now*,
  so between the height write and the layout pass it names the row the caret
  is leaving — deleting a newline painted it a row high and dropped it back a
  frame later. `_terminal_cursor_offset` corrects for the rows still in
  flight (`_pending_resize_delta`: the input is bottom-pinned, and its own
  scroll is about to be zero because the height only changes while the
  wrapped text fits). It is applied from `_watch_selection` too, since vim
  moves the cursor *after* the edit that resized the input.
- **Input scrolling past `MAX_INPUT_HEIGHT`**: the input keeps
  `scrollbar-size-vertical: 0` like the log — a visible scrollbar would take
  two columns off the wrap width and re-wrap everything already typed at the
  moment the cap is crossed. `ChatInput.edit` re-runs `scroll_cursor_visible`
  after `super().edit()`: TextArea scrolls to the cursor mid-edit, before
  `_refresh_size` updates the virtual size, so past the cap the caret would
  trail the typed line by a frame.
- **Interrupt = worker cancellation**: Ctrl+C (priority binding, so it
  pre-empts Textual's own `ctrl+c` → `screen.copy_text`) forks in
  `action_interrupt_or_quit`: cancel the turn worker while streaming, else
  copy `screen.get_selected_text()` via `copy_to_clipboard` (OSC 52) and
  clear the selection, else arm exit for `CTRL_C_EXIT_WINDOW` and quit on a
  second press. Only a bare press arms, so repeated copies can't quit.
  `chat_async` maps `CancelledError` to mark-interrupted + finalize before
  re-raising.
- Post-turn save + smart titling run in `asyncio.to_thread` — `LLMClient.chat`
  (sync) is called there for titles, which is why its SIGINT handler installs
  only on the main thread.
- **Look**: Claude-Code-style, TUI-only (the scrollback UI keeps its labels).
  Marker glyphs instead of role labels — `LABEL_MARKERS` maps `ui/labels.py`
  labels to `❯` (user, dim in history) / `●` (assistant, default foreground) /
  `✱` (system prompt, shown only when non-empty); info/warning/error have NO
  marker and render as bare dim/yellow/red text lines (`_notice_widget`) —
  plus a dim one-line header (`oi · model (sub|api)[ · search] · …`, the
  search segment present only when search is on *and* the model supports it,
  mirroring the client's own drop; `/search` rewrites the header in place via
  `_refresh_header`, so the message count is snapshotted at mount rather than
  re-read) instead of the
  banner. The input is pinned at the bottom with top/bottom border rules only
  (no side borders/padding, so its `❯` — full brightness — column-aligns with
  the history markers) and a 1-row hint line below it (`esc to interrupt`
  while streaming, empty when idle — fixed height so it never shifts layout).
  `ansi-dark` theme +
  `ansi_*`/`ansi_default` CSS colors keep the terminal palette. Markdown
  tables get `width: auto` (Textual's default `1fr` stretches them across the
  pane and flips their grid to `expand`); they still shrink-to-fit when the
  content is wider than the pane. Thinking
  stays plain grey-italic text (a `Static`, not markdown). The log is a
  fixed-height (`1fr`) `VerticalScroll` with `anchor()`: v8's compositor
  bottom-aligns anchored content even when it's shorter than the container
  (negative scroll), so the conversation hugs the input chat-app-style —
  deliberate; user scroll releases the anchor.
- **Slash commands**: executed in `_handle_local_command` (`/btw` streams a
  side answer in the turn worker under the hollow `○` marker — nothing
  appended or saved; `/bookmark` mirrors the CLI; `/vim` toggles vim mode).
  Typing `/` opens `SlashMenu` (`tui/slash_menu.py`), driven from
  `TextArea.Changed` via `local_commands.get_slash_prefix` (the same
  predicate the prompt_toolkit completer uses). Tab/Ctrl+N/Ctrl+P are
  intercepted in `ChatInput._on_key` and posted as semantic `MenuKey`
  messages for the app to act on.
- **Image paste**: `Alt+V` or `Ctrl+V` (app bindings, no-op without `supports_vision`; `Ctrl+V` is priority to pre-empt TextArea's internal-clipboard paste, and exists for Mac terminals — see the paste-pills section)
  reads the clipboard via `ui/image_paste.py:read_clipboard_image` in a
  thread; `ChatInput` owns the images. `attach_image` inserts a literal
  `[Image #N]` marker; a reconcile pass on every text change drops images
  whose marker was edited away and renumbers survivors from 1 (numbering is
  per turn — `consume_content()` splices `BinaryContent` in at submit and
  resets). Markers are atomic in every mode: `_watch_selection` snaps the
  cursor out of a marker (so arrows/Home/End/mouse and typing can't land
  inside — guard it with `getattr`, the reactive fires during
  `TextArea.__init__`), Backspace/Delete remove one whole, and `ChatInput`
  hands `VimHandler` an `atom_spans` callback so vim treats them as single
  characters (`_expand_range` grows any clipping edit to cover them).
  `vim.py` stays image-agnostic — it only knows "atoms". No PUA sentinels in
  the TUI. Markers render as cyan pills (`PILL_RICH_STYLE`, same look as the
  scrollback `PillProcessor`): in the input via `ChatInput.get_line`, TextArea's
  per-line styling hook, and in echoed/replayed messages via `_pill_text`.
  Anything that stylizes whole cells *after* `get_line` would flatten that
  color, so the cursor line and bracket matching are off and the painted cursor
  is suppressed with a null `cursor_style` theme rather than neutral CSS (the
  visible cursor is the terminal's own anyway).
- **Vim mode**: `tui/vim.py` — `VimHandler`, a modal key dispatcher over
  TextArea primitives (document index/location conversion, selection, edit
  methods, undo stack); motions/text objects are pure `(text, index)`
  functions with direct unit tests. `ChatInput` delegates printable keys to
  it when mode != insert. Esc is resolved **inside `ChatInput._on_key`**,
  synchronously, with precedence slash-menu-close (posted to the app) → vim
  (insert/visual → normal, or clear pending) → interrupt-stream (posted):
  routing the mode change through the app's message queue would apply it
  *after* the keys typed right behind Esc, which then insert as text.
  `/vim` toggles it live and persists via
  `update_user_config`; submit resets to insert mode. `VimModeChanged`
  carries `None` when vim is off, which drives both the cursor shape and the
  hint line's `-- INSERT --`/`-- VISUAL --` indicator (nothing in normal
  mode, vim's own convention).
- **Cursor**: the input uses the terminal's hardware cursor; the painted cell
  is never drawn (`_NO_CURSOR_THEME` gives `TextArea` an empty `cursor_style`,
  which makes it skip the stylize entirely).
  Textual already keeps `app.cursor_position` at the TextArea cursor (IME
  support) and moves the terminal cursor there after every frame inside the
  synchronized update, so `OiApp` just shows it (`?25h`) and shapes it via
  DECSCUSR on `ChatInput.VimModeChanged`: steady bar (`6 q`) in insert,
  steady block (`2 q`) otherwise, `0 q` reset on exit. Writes go through
  `_write_terminal`, which no-ops headless — screenshots/tests show no
  cursor, that's expected.
- Gotchas: don't name `OiApp` attributes `_registry` or `_log` (Textual
  App/DOMNode internals). Textual is pinned `>=8.2.8,<9` (fast-moving
  majors). `OiApp.__init__` sets `_disable_tooltips` (Textual private, also
  what `run_test` uses) so the `Tooltip` widget is never mounted: Textual
  gives every markdown table cell a tooltip repeating that cell's own text,
  and oi sets no tooltips of its own. `scroll_sensitivity_y` is dropped to
  `1.0` there too: it counts lines per wheel *event* and terminals send three
  events per notch, so Textual's default of 2 scrolls six lines a notch.
- Tests drive the app headlessly via `app.run_test()` + a fake client
  (`tests/unit/tui/test_app.py`); `app.export_screenshot()` renders an SVG if
  you need to eyeball layout. `run_test` overrides `_disable_tooltips`, so
  tooltip behavior can only be asserted on an app that isn't running.

**Startup Latency (`warmup.py`):**
- pydantic_ai's package `__init__` costs ~600ms (it eagerly pulls in
  mcp/fastmcp/logfire), so nothing on the startup path imports it at module
  level: the modules that use it hold `TYPE_CHECKING` imports for annotations
  and function-local imports at the runtime call sites.
  `tests/unit/test_startup.py` guards this in a subprocess.
- The interactive frontends pre-import it in the background once their UI is
  up — TUI via `call_after_refresh(warmup.warm)` after the first paint,
  scrollback at the top of `run_chat_loop` — so the first turn normally finds
  it loaded. Headless/stats/docs/auth never warm; they import inline.
- A plain pydantic_ai import racing the warm-up thread **crashes**: Python
  resolves cross-thread import cycles by exposing partially initialized
  modules, and pydantic_ai has internal cycles. Every path that can be the
  process's first pydantic_ai touch after `warm()` therefore gates on
  `warmup.ensure()` (a lock shared with the warm thread): turn start in both
  frontends, `/btw`, and image paste. Everything else runs downstream of
  those gates or before `warm()` is called (chat load, selector search).
  Don't add a module-level pydantic_ai import or a new ungated first touch.
- `message_utils` helpers early-return before their import when the history
  is empty, so a new chat's TUI mount (which replays an empty history on the
  main thread, pre-gate) never triggers it.

**Model Construction (`client.py:_resolve_model`):**
- pydantic-ai's `infer_model()` imports the provider SDK and builds an HTTP
  client — ~300ms on the first turn of a run, tens of ms after — and it runs
  synchronously inside `model_request_stream`. `_stream_model_response`
  therefore resolves the model itself, in `asyncio.to_thread`, so the TUI's
  event loop keeps painting while it happens.
- Resolved models are cached **per event loop**, because the HTTP client binds
  to the loop that used it: `chat()` runs each turn on its own `asyncio.run`
  loop and so rebuilds every time, while the TUI's long-lived loop reuses the
  model and keeps its connection warm between turns. Never make the cache
  loop-agnostic. Subscription models are already `Model` instances and pass
  through untouched (they must stay per-turn — the access token can refresh).

**Streaming & Output:**
- `StyledRenderer` is the scrollback renderer — styled thinking traces, NOT markdown rendering (markdown is TUI-only); `ResponseHandler` accepts an injected renderer (the TUI passes `TuiRenderer` via `chat_async`'s `renderer_factory`)
- Shared label/color definitions live in `ui/labels.py` and are reused by plain prints, Rich output, and the prompt label
- Rich console with `highlight=False` to prevent number styling in LLM output
- Real-time streaming with interrupt handling

**Paste Pills (images + long text):**
- One unified `PasteStore` in `ui/image_paste.py` allocates Unicode PUA sentinel chars for both kinds of pastes; one sentinel per entry so backspace/vim `x`/word motions treat the pill atomically
- `PillProcessor` expands each sentinel at display time: image sentinels render as `[Image #N] `, text-paste sentinels as `[Paste #N (L lines)] `. Images and pastes are numbered independently, by first occurrence order in the buffer, so deleting one renumbers the rest
- Image path: `Alt+V` or `Ctrl+V` reads the clipboard (via `wl-paste` / `xclip` / `pngpaste`-style readers) and inserts an image sentinel. Bindings are only registered when the active model has `supports_vision: true`. `Ctrl+V` is the Mac chord: Mac terminals paste with `Cmd+V` so `Ctrl+V` reaches the app there (and Option isn't Alt by default, so `Alt+V` doesn't arrive); Linux terminals that hijack `Ctrl+V` for `paste_from_clipboard` (Ghostty, Konsole, …) never deliver it, which makes the extra binding inert there and `Alt+V` the Linux chord
- Long-text path: `Keys.BracketedPaste` is intercepted in `InputHandler`; pastes that hit `PASTE_LINE_THRESHOLD` (6 lines) **or** `PASTE_CHAR_THRESHOLD` (400 chars) become a single text-paste sentinel, shorter pastes are inserted verbatim. The char limit catches long single-line paragraphs that wrap across many rendered rows. This works around a prompt_toolkit limitation — its diff-based renderer can't progressively commit rows to the scrollback buffer, so any content that scrolls past terminal height gets permanently clobbered. Pills keep the buffer visually short. The pill label still shows lines (source lines match the user's mental model of what they copied, even though chars drive the trigger)
- On submit, `PasteStore.split()` walks the buffer: text-paste sentinels expand inline into the surrounding text, image sentinels become `BinaryContent` parts. Returns `str` (text-only) or `list[str | BinaryContent]` (mixed). `UserPromptPart` takes either; pydantic-ai passes through to providers
- `flatten_history` only needs to handle images (text pastes are just text by submit time) — renders `[Image #N]` placeholders for replay of mixed-content messages

**Local Slash Commands:**
- Local in-chat commands are defined in `local_commands.py`, not inline in `InputHandler`
- `InputHandler` wires slash command completion through prompt-toolkit
- Slash command completion uses `CompleteStyle.READLINE_LIKE`, so completion is `Tab`-triggered and rendered in a readline-like way instead of a dropdown menu
- Unknown slash commands are still rejected in `app.py` after submit so they never get sent to the model
- **`/vim` and `/tui`** are pure config toggles, driven by the `TOGGLE_SETTINGS`
  table + `toggle_setting()` in `app.py` (shared by both frontends; the TUI's
  handler only adds the live `set_vim_enabled` call). A `ToggleSetting.key` is
  both the `Config` attribute and the `config.json` key, so adding a toggle is
  one table entry plus a `LocalCommandSpec`. On a failed write the setting still
  applies to the session and the notice says so
- **`/search`** turns web search on for the rest of the session (`enable_search()`
  in `app.py`, shared by both frontends) by flipping `ctx.chat_options`, so it
  applies from the next turn without a relaunch. It is deliberately **one-way**:
  there is no off, because turning search back off has never been wanted in
  practice — the flow it exists for is "this needs current information" partway
  into a chat. Each flip also invalidates the whole Anthropic prompt cache (the
  `tools` array is the front of the hashed prefix, and
  `anthropic_cache_tool_definitions` puts a breakpoint right there), so one-way
  pays that once. Replaying `web_search_tool_result` blocks with the tool no
  longer declared is *not* a constraint here — that was tested and works.
  Models without `supports_search` get a warning and no state change, matching
  the client's own silent drop
- **`/btw <question>`** is the one command that takes an argument and runs a full
  model turn. `_run_side_question` (`app.py`) streams a normal reply against a
  *copy* of `current_chat.messages` (plus the question, and the pending system
  prompt if the chat is brand-new) — nothing is appended or saved, so the side
  exchange leaves no trace in history. It reuses the session's `ChatOptions`
  (search/thinking inherited) with `assistant_label_text=BTW_AI_LABEL_TEXT` so
  the answer renders under an `AI (btw):` label. It must swallow `KeyboardInterrupt`
  locally — otherwise Ctrl+C mid-answer reaches the loop's idle-exit handler and
  quits oi. The label override rides through `ChatOptions.assistant_label_text` →
  `rich_label(AI_LABEL, text=...)` in `renderers.py`.

**Key Components:**
- `LLMClient` (core/client.py) - High-level API client with retry logic
- `codex_auth` (core/codex_auth.py) - ChatGPT subscription login, token store/refresh, Codex routing client, rate-limit/exhaustion state
- `ChatManager` (core/chat_manager.py) - Session persistence & management
- `Chat`/`ChatMetadata` (core/session.py) - Data models
- `ChatSelector` (ui/chat_selector.py) - Interactive chat selection (+ search/preview/editor)
- `ui/transcript.py` - Shared plaintext/styled/search views of a chat
- `InputHandler` (ui/input_handler.py) - User input handling
- `local_commands.py` - Slash command definitions + completion helpers
- `ui/labels.py` - Shared label text and styling helpers
- `ModelRegistry` (registry.py) - Central model/provider management
- `ResponseHandler` (response_handler.py) - Streaming coordination

**Main Function Structure:**
Located in `app.py`, broken into logical functions:
- `parse_arguments()` - CLI parsing (from cli.py)
- `setup_configuration()` - Returns a `ChatLoopContext` bundling all components
- `handle_chat_selection()` - Chat loading
- `Chat.create_new()` - New session creation (classmethod on `Chat`)
- `run_chat_loop(chat, ctx)` - Main interaction (takes `ChatLoopContext`)
- `run_headless_turn(args, ctx, registry)` - Single-turn headless path (used when `-p` is set)
- `main()` - High-level orchestration

**Key Constants:**
Mostly centralized in `constants.py`:
- `MIN_MESSAGES_FOR_SMART_TITLE = 8`
- `DEFAULT_PAGE_SIZE = 10` 
- UI navigation keys

Conversation and status labels are centralized in `ui/labels.py`:
- `USER_LABEL`, `AI_LABEL`, `SYSTEM_LABEL`
- `INFO_LABEL`, `WARNING_LABEL`, `ERROR_LABEL`

**Common Gotchas:**
1. Add models to `models.yaml`, not provider classes
2. `StyledRenderer` is for styled thinking traces, NOT markdown rendering
3. Default model from YAML `aliases.default`, not hardcoded
4. No bespoke provider classes—add/update models via YAML aliases instead
5. Thinking traces:
   - OpenAI reasoning models automatically receive `openai_reasoning_summary="detailed"` when thinking is enabled so we can render their reasoning summaries.
   - OpenAI reasoning models also set `openai_reasoning_effort="medium"` by default to satisfy the API requirement.
   - Anthropic models default to `anthropic_thinking={"type": "adaptive"}` when thinking is enabled (via `setdefault` in client.py). Adaptive thinking means the model decides how much to think.
   - **Claude Haiku 4.5** overrides this via `extra_params: {anthropic_thinking: {type: enabled, budget_tokens: 2048}}` in `models.yaml` because it still requires the explicit budget.
   - Because `extra_params` are merged unconditionally, a pinned budget like Haiku's would otherwise force thinking on even when `enable_thinking` is False. `client.py` therefore sets `anthropic_thinking={"type": "disabled"}` for Anthropic when thinking is off, so `--no-thinking` (and the smart-title call) are honored regardless of the model's pinned budget.
   - Google Gemini models default to `google_thinking_config={"include_thoughts": True}` when thinking is enabled so their thoughts stream into the UI.
6. Reasoning-focused OpenAI models (gpt-5, o-series) should be defined under the `openai-responses` provider section so the Responses API (with thinking traces) is used.
7. `--search` wires up Pydantic AI's `WebSearchTool` only for providers that support it (OpenAI Responses, Anthropic, Gemini, xAI), plus `WebFetchTool(optional=True)` so the model can also read a specific URL. Anthropic and Google take a separate fetch tool; OpenAI's and xAI's web search opens pages itself, and `optional` makes pydantic-ai drop the tool there instead of raising `UserError`. Which wire version Anthropic gets (`web_search_20260209` with dynamic filtering vs the basic `_20250305`) is decided by pydantic-ai's model profile — never hand-write the type string, and don't add a `code_execution` tool alongside the `_20260209` variants (dynamic filtering already runs code server-side). OpenRouter models automatically switch to their `:online` variant and add the `web` plugin so search works there too; other providers simply ignore the flag.
8. Rich console has `highlight=False` to prevent auto-styling numbers
9. User config functions (`load_user_config`, `update_user_config`) live in `config/settings.py`, not a separate file
10. Prompts loaded from `src/oi/prompts/` directory, not a Python package
10. Custom exceptions in `exceptions.py` for proper error handling
11. Conversation/status label text and colors live in `ui/labels.py`, not `constants.py`
12. Local slash commands are completed from `local_commands.py`; if you add one, update the command registry there
13. Slash command completion is readline-like `Tab` completion, not a dropdown selector UI
14. Paste pills (both images and long text) use Unicode PUA sentinel chars in the input buffer; display-only pill expansion via a prompt_toolkit `Processor`. Text pastes expand inline on submit, images become `BinaryContent` parts. Long-text threshold is a fixed `PASTE_LINE_THRESHOLD` in `input_handler.py` (not a function of terminal size — the true failure mode is rendered-rows vs scrollback, which a source-line threshold can only approximate, so the constant is the honest choice). `Ctrl+V` is bound as the Mac image-paste chord — Linux terminals that hijack it for paste never deliver it, so it's inert there
15. Use the `google` provider prefix for Gemini models in `models.yaml` (the `google-gla`/`google-vertex` prefixes are deprecated in pydantic-ai and removed in v2)
16. ChatGPT subscription billing routes through a constructed `OpenAIResponsesModel` **instance** (custom Codex provider), the only path that passes a `Model` object instead of a `provider:model` string to `model_request_stream` — the retry helper tells them apart via `isinstance(model, str)`. Don't add an `openai-codex` provider to `models.yaml`; eligibility is the `supports_subscription` flag. Codex requires `openai_store=False` and a present `instructions` key (see the Subscription Billing section).
17. Pydantic AI 2.x renamed two provider prefixes: bare `openai:` now means the **Responses** API (Chat Completions is `openai-chat:`), and xAI is `xai:` (the `grok:` alias is gone). Both silently change what a `models.yaml` entry resolves to, so check the prefix when touching model config.
18. Pydantic AI 2.x ships provider SDKs as **opt-in extras**; the meta package only bundles anthropic/google/openai (plus cli/mcp/web/…). `pyproject.toml` therefore requests `pydantic-ai[groq,xai]`. OpenAI-compatible providers (deepseek, openrouter, moonshotai, together, …) ride on the `openai` extra and need nothing; adding a model from bedrock/cohere/mistral/huggingface means adding that extra to the dependency too, or it raises `ImportError` at model resolution.

**Quick Tests:**
```bash
uv run oi --help   # Smoke test
uv run python -c "from oi.registry import ModelRegistry; print(list(ModelRegistry().get_available_models().keys()))"  # Test model loading

# Headless e2e smoke — `--ephemeral` guarantees no chat dir is written or modified.
# Use a cheap/fast model and `--no-thinking` for deterministic, grep-friendly output.
uv run oi -p "say only the word PONG" --ephemeral -m haiku --no-thinking
```

Do NOT reach for `-c --ephemeral -p "..."` as a casual smoke test — `-c` loads the user's actual latest chat, so even with `--ephemeral` (no save) you still send their full real conversation to the API and then prompt the model with something unrelated. Waste of tokens, confusing for the model. If you really need to exercise the multi-turn path, create an explicit fixture chat first.
