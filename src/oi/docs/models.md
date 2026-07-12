# Adding models to oi

This document is written for LLM agents configuring oi on a user's behalf
(humans welcome too).

oi builds its model list from two YAML files:

1. **Built-in defaults**, shipped inside the installed package (read-only;
   reproduced at the bottom of this document).
2. **Your user config**: `$user_models_path` ($user_models_status)

The user config is deep-merged over the built-ins at the model-property level,
so it only needs deltas: add new models, add aliases, or override single
properties of a built-in model (e.g. set `extra_params` without repeating its
capability flags). Top-level keys starting with `_` are ignored — useful for
YAML anchors. `oi --user-paths` prints every config location.

## Config shape

Every top-level section except `aliases` is a **pydantic-ai provider prefix**,
and each key under it is that provider's **model id**. The two joined as
`<provider>:<model-id>` must form a valid pydantic-ai **2.x** model name — the
set of valid providers and ids is defined by pydantic-ai, not by oi.

```yaml
<provider>:                   # pydantic-ai provider prefix, e.g. anthropic
  <model-id>:                 # provider's model id, e.g. claude-sonnet-5
    supports_thinking: true   # model can emit reasoning traces (default: false)
    supports_search: true     # --search web search works (default: false)
    supports_vision: true     # accepts image input (default: false)
    max_tokens: 64000         # optional output-token cap
    extra_params:             # optional, merged into pydantic-ai ModelSettings
      openai_reasoning_effort: high   # (example) provider-specific settings

aliases:
  <alias>: <provider>/<model-id>   # note the slash here, not a colon
  default: <alias-or-model-id>     # the model oi starts with
```

A model with no special settings is just `<model-id>: {}`.

## pydantic-ai 2.x naming traps

- Bare `openai:` routes to the **Responses API** in pydantic-ai 2.x; Chat
  Completions is `openai-chat:`. Put OpenAI reasoning models (gpt-5.x,
  o-series) under `openai-responses:` so thinking traces work.
- xAI is `xai:` — the old `grok:` prefix was removed. Google is `google:` —
  `google-gla:` / `google-vertex:` were removed.
- OpenAI-compatible providers (openrouter, deepseek, moonshotai, together, …)
  work out of the box. Some others (bedrock, cohere, mistral, huggingface, …)
  need their pydantic-ai extra installed first:
  `uv tool install oi-chat --with 'pydantic-ai[mistral]'` or
  `pipx inject oi-chat 'pydantic-ai[mistral]'`.

## API keys

Each provider reads its standard environment variable: `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY`,
`DEEPSEEK_API_KEY`, `GROQ_API_KEY`, and so on. Keys can also live in oi's env
file, which overrides the inherited environment: `$env_file_path`

If the provider needs a key that isn't set yet, do NOT ask the user to paste
it into the conversation — a key shared with an LLM may end up in logs or
training data. Finish all the config work first, then tell the user to add the
key themselves, e.g.:

```
echo 'MISTRAL_API_KEY=...' >> $env_file_path
```

## Verify

After editing the config, confirm the model resolves and answers. This is a
single ephemeral turn — nothing is saved to chat history:

```
oi -m <alias-or-model-id> -p "say only PONG" --ephemeral --no-thinking
```

## Built-in defaults (the merge base)

Shipped read-only inside the package; shown here so you can see what already
exists and what your config merges over. Do **not** copy it wholesale into the
user config — that would pin the user to today's model list and mask future
updates. Add only deltas.

```yaml
$default_config
```
