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
3. Commit and push `main`.
4. `git tag vX.Y.Z && git push origin vX.Y.Z` — the workflow does the rest.

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
│   └── stats_view.py  # Rich rendering for `oi stats` (heatmap, bars)
├── llm_types.py       # Shared chat/model capability dataclasses
├── app.py             # Main application orchestration + ChatLoopContext
├── cli.py             # Command-line argument parsing
├── main.py            # Entry point (delegates to app.py)
├── constants.py       # All constants & UI config
├── exceptions.py      # Custom exception classes
├── local_commands.py  # Local in-chat slash command registry + completion
├── prompts.py         # Prompt file loading
├── registry.py        # ModelRegistry - alias + capability management (single config load)
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

**Streaming & Output:**
- `StyledRenderer` is the only renderer — provides styled thinking traces (NOT markdown rendering!)
- Shared label/color definitions live in `ui/labels.py` and are reused by plain prints, Rich output, and the prompt label
- Rich console with `highlight=False` to prevent number styling in LLM output
- Real-time streaming with interrupt handling

**Paste Pills (images + long text):**
- One unified `PasteStore` in `ui/image_paste.py` allocates Unicode PUA sentinel chars for both kinds of pastes; one sentinel per entry so backspace/vim `x`/word motions treat the pill atomically
- `PillProcessor` expands each sentinel at display time: image sentinels render as `[Image #N] `, text-paste sentinels as `[Paste #N (L lines)] `. Images and pastes are numbered independently, by first occurrence order in the buffer, so deleting one renumbers the rest
- Image path: `Alt+V` reads the clipboard (via `wl-paste` / `xclip`) and inserts an image sentinel. Binding is only registered when the active model has `supports_vision: true`. `Ctrl+V` is unusable because most terminals (Ghostty, Konsole, iTerm2, …) hijack it for `paste_from_clipboard`
- Long-text path: `Keys.BracketedPaste` is intercepted in `InputHandler`; pastes that hit `PASTE_LINE_THRESHOLD` (6 lines) **or** `PASTE_CHAR_THRESHOLD` (400 chars) become a single text-paste sentinel, shorter pastes are inserted verbatim. The char limit catches long single-line paragraphs that wrap across many rendered rows. This works around a prompt_toolkit limitation — its diff-based renderer can't progressively commit rows to the scrollback buffer, so any content that scrolls past terminal height gets permanently clobbered. Pills keep the buffer visually short. The pill label still shows lines (source lines match the user's mental model of what they copied, even though chars drive the trigger)
- On submit, `PasteStore.split()` walks the buffer: text-paste sentinels expand inline into the surrounding text, image sentinels become `BinaryContent` parts. Returns `str` (text-only) or `list[str | BinaryContent]` (mixed). `UserPromptPart` takes either; pydantic-ai passes through to providers
- `flatten_history` only needs to handle images (text pastes are just text by submit time) — renders `[Image #N]` placeholders for replay of mixed-content messages

**Local Slash Commands:**
- Local in-chat commands are defined in `local_commands.py`, not inline in `InputHandler`
- `InputHandler` wires slash command completion through prompt-toolkit
- Slash command completion uses `CompleteStyle.READLINE_LIKE`, so completion is `Tab`-triggered and rendered in a readline-like way instead of a dropdown menu
- Unknown slash commands are still rejected in `app.py` after submit so they never get sent to the model

**Key Components:**
- `LLMClient` (core/client.py) - High-level API client with retry logic
- `ChatManager` (core/chat_manager.py) - Session persistence & management
- `Chat`/`ChatMetadata` (core/session.py) - Data models
- `ChatSelector` (ui/chat_selector.py) - Interactive chat selection
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
   - Google Gemini models default to `google_thinking_config={"include_thoughts": True}` when thinking is enabled so their thoughts stream into the UI.
6. Reasoning-focused OpenAI models (gpt-5, o-series) should be defined under the `openai-responses` provider section so the Responses API (with thinking traces) is used.
7. `--search` wires up Pydantic AI's `WebSearchTool` only for providers that support it (OpenAI Responses, Anthropic, Gemini, xAI). OpenRouter models automatically switch to their `:online` variant and add the `web` plugin so search works there too; other providers simply ignore the flag.
8. Rich console has `highlight=False` to prevent auto-styling numbers
9. User config functions (`load_user_config`, `update_user_config`) live in `config/settings.py`, not a separate file
10. Prompts loaded from `src/oi/prompts/` directory, not a Python package
10. Custom exceptions in `exceptions.py` for proper error handling
11. Conversation/status label text and colors live in `ui/labels.py`, not `constants.py`
12. Local slash commands are completed from `local_commands.py`; if you add one, update the command registry there
13. Slash command completion is readline-like `Tab` completion, not a dropdown selector UI
14. Paste pills (both images and long text) use Unicode PUA sentinel chars in the input buffer; display-only pill expansion via a prompt_toolkit `Processor`. Text pastes expand inline on submit, images become `BinaryContent` parts. Long-text threshold is a fixed `PASTE_LINE_THRESHOLD` in `input_handler.py` (not a function of terminal size — the true failure mode is rendered-rows vs scrollback, which a source-line threshold can only approximate, so the constant is the honest choice). Don't bind `Ctrl+V` — terminals hijack it for paste
15. Use the `google` provider prefix for Gemini models in `models.yaml` (the `google-gla`/`google-vertex` prefixes are deprecated in pydantic-ai and removed in v2)

**Quick Tests:**
```bash
uv run oi --help   # Smoke test
uv run python -c "from oi.registry import ModelRegistry; print(list(ModelRegistry().get_available_models().keys()))"  # Test model loading

# Headless e2e smoke — `--ephemeral` guarantees no chat dir is written or modified.
# Use a cheap/fast model and `--no-thinking` for deterministic, grep-friendly output.
uv run oi -p "say only the word PONG" --ephemeral -m haiku --no-thinking
```

Do NOT reach for `-c --ephemeral -p "..."` as a casual smoke test — `-c` loads the user's actual latest chat, so even with `--ephemeral` (no save) you still send their full real conversation to the API and then prompt the model with something unrelated. Waste of tokens, confusing for the model. If you really need to exercise the multi-turn path, create an explicit fixture chat first.
