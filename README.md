# llm-cli

Command-line chat interface for multiple LLM providers, with streaming output, persistent chats, and YAML-based model aliases.

![Demo](demo.gif)

## Highlights

- One CLI for multiple providers through `pydantic-ai` (`openai`, `openai-responses`, `anthropic`, `google-gla`, `openrouter`, `moonshotai`, and others you add in YAML).
- Fast model switching via aliases like `sonnet`, `gpt`, `gemini-pro`.
- Chat history with resume/continue flows and an interactive selector.
- Chat bookmarks with `/bookmark` in-chat plus bookmark/filter controls in the selector.
- Optional search and thinking traces when the selected model supports them.
- User config in `~/.config/llm_cli/` merges with built-in defaults.

## Install

```bash
uv tool install git+https://github.com/dansclearov/llm-cli.git
```

For local development:

```bash
git clone https://github.com/dansclearov/llm-cli.git
cd llm-cli
uv install --group dev
uv run llm-cli --help
```

## Quick Start

Set one or more provider API keys (you only need keys for providers you use):

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export OPENROUTER_API_KEY=...
```

Start chatting:

```bash
llm-cli
llm-cli concise -m sonnet
```

## Common Commands

```bash
# Pick prompt + model
llm-cli concise -m gpt

# Continue or resume chats
llm-cli -c
llm-cli -r
llm-cli -r chat_20240622_143022_a1b2c3d4

# In-chat local commands
/bookmark
/vim

# Slash commands complete with Tab

# Model features
llm-cli --search -m sonnet
llm-cli --no-thinking -m gpt
llm-cli --hide-thinking -m gpt

# Show config/data paths
llm-cli --user-paths
```

## Configuration

Default models and aliases live in `src/llm_cli/models.yaml`.
User overrides live in `~/.config/llm_cli/models.yaml` and are merged on top of defaults.

Example user overrides:

```yaml
aliases:
  default: r1
  r1: openrouter/deepseek/deepseek-r1-0528

openrouter:
  deepseek/deepseek-r1-0528:
    supports_thinking: true
    supports_search: true
```

Notes:

- Top-level keys starting with `_` are ignored (useful for YAML anchors/metadata).
- `extra_params` is passed through for provider-specific model settings.
- On first run, `~/.config/llm_cli/models.yaml` is auto-created from a template.

## Prompts

Prompts are loaded from:

1. `~/.config/llm_cli/prompts/` (user overrides)
2. `src/llm_cli/prompts/` (built-ins)

Naming format is `prompt_<name>.txt`, used as `llm-cli <name>`.

Set the default prompt for new chats in `~/.config/llm_cli/config.json`:

```json
{
  "default_prompt": "concise"
}
```

An explicit positional prompt still wins, for example `llm-cli general`.

## Development

```bash
uv run pytest
uv run ty check
```

## License

MIT. See `LICENSE`.
