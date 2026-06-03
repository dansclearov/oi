# oi

Command-line chat interface for multiple LLM providers, with streaming output, persistent chats, and YAML-based model aliases.

![Demo](https://raw.githubusercontent.com/dansclearov/oi/main/demo.gif)

## Highlights

- One CLI for multiple providers through `pydantic-ai` (`openai`, `openai-responses`, `anthropic`, `google`, `openrouter`, `moonshotai`, and others you add in YAML).
- Fast model switching via aliases like `sonnet`, `gpt`, `gemini-pro`.
- Chat history with resume/continue flows and an interactive selector that can search, preview, and open chats in your editor (`$EDITOR`).
- Chat bookmarks with `/bookmark` in-chat plus bookmark/filter controls in the selector.
- Optional search and thinking traces when the selected model supports them.
- Paste images with `Alt+V` on vision-capable models.
- User config in `~/.config/oi/` merges with built-in defaults.

## Install

```bash
uv tool install oi-chat   # recommended
# or
pipx install oi-chat
```

For local development:

```bash
git clone https://github.com/dansclearov/oi.git
cd oi
uv install --group dev
uv run oi --help
```

## Quick Start

Set one or more provider API keys (you only need keys for providers you use):

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export OPENROUTER_API_KEY=...
```

Alternatively, bill OpenAI models to a ChatGPT subscription instead of an API
key with `oi auth openai login`; eligible models then route through the
subscription automatically (set `OI_NO_SUBSCRIPTION=1` to force the API key).

Start chatting:

```bash
oi
oi -P concise -m sonnet
```

## Common Commands

```bash
# Pick prompt + model
oi -P concise -m gpt

# Continue or resume chats
oi -c
oi -r
oi -r chat_20240622_143022_a1b2c3d4

# Headless: send one message and exit
oi -p "what's 2+2"
oi -c -p "follow up on the last chat"
oi --ephemeral -p "quick question, don't save it"
oi -c --ephemeral -p "probe an existing chat without dirtying it"

# In-chat local commands
/bookmark
/vim

# Slash commands complete with Tab

# Model features
oi --search -m sonnet
oi --no-thinking -m gpt
oi --hide-thinking -m gpt

# Show config/data paths
oi --user-paths
```

## Configuration

Default models and aliases live in `src/oi/models.yaml`.
User overrides live in `~/.config/oi/models.yaml` and are merged on top of defaults.

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
- On first run, `~/.config/oi/models.yaml` is auto-created from a template.

## Prompts

Prompts are loaded from:

1. `~/.config/oi/prompts/` (user overrides)
2. `src/oi/prompts/` (built-ins)

Naming format is `prompt_<name>.txt`, used as `oi -P <name>`.

Set the default prompt for new chats in `~/.config/oi/config.json`:

```json
{
  "default_prompt": "concise"
}
```

An explicit `-P` still wins, for example `oi -P general`.

## Development

```bash
uv run pytest
uv run ty check
```

## License

MIT. See `LICENSE`.
