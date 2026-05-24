# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html);
while pre-1.0, minor version bumps may include breaking changes (CLI flags,
config/`models.yaml` format, alias names).

## [Unreleased]

### Fixed

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
