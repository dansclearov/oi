# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html);
while pre-1.0, minor version bumps may include breaking changes (CLI flags,
config/`models.yaml` format, alias names).

## [Unreleased]

### Changed

- The interactive chat selector (`oi -r`) now adapts to the terminal size: the
  number of rows shown fits the height, and rows, the header, and the help line
  reflow to the width instead of wrapping. This makes it usable on narrow
  screens, e.g. a phone over SSH.

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

[Unreleased]: https://github.com/dansclearov/oi/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/dansclearov/oi/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/dansclearov/oi/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dansclearov/oi/releases/tag/v0.1.0
