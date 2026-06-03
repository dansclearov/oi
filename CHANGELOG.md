# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html);
while pre-1.0, minor version bumps may include breaking changes (CLI flags,
config/`models.yaml` format, alias names).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/dansclearov/oi/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dansclearov/oi/releases/tag/v0.1.0
