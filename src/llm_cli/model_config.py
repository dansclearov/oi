from functools import lru_cache
from typing import Any

from llm_cli.config.loaders import load_merged_model_config
from llm_cli.ui.labels import WARNING_LABEL, ansi_message


@lru_cache(maxsize=1)
def load_model_capabilities() -> dict[str, dict[str, dict[str, Any]]]:
    """Load model capabilities from YAML config, merging user config with package config."""
    merged_config = load_merged_model_config()
    return {
        provider: models
        for provider, models in merged_config.items()
        if provider != "aliases" and isinstance(models, dict)
    }


def clear_model_capabilities_cache() -> None:
    """Clear the in-memory capabilities cache (used in tests)."""
    load_model_capabilities.cache_clear()


def _normalize_max_tokens(value: Any) -> int | None:
    """Return a positive integer max_tokens value when configured."""
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        print(
            ansi_message(
                WARNING_LABEL,
                f"Invalid max_tokens value {value!r} in models config; ignoring.",
            )
        )
        return None
    return value


def get_model_capabilities(provider_name: str, model_id: str) -> dict[str, Any]:
    """Get capabilities for a specific model with defaults."""
    config = load_model_capabilities()

    # Get model config or empty dict
    model_entry = config.get(provider_name, {}).get(model_id, {})
    model_config = model_entry if isinstance(model_entry, dict) else {}
    extra_params = model_config.get("extra_params", {})
    safe_extra_params = extra_params if isinstance(extra_params, dict) else {}

    # Apply defaults
    return {
        "supports_search": model_config.get("supports_search", False),
        "supports_thinking": model_config.get("supports_thinking", False),
        "max_tokens": _normalize_max_tokens(model_config.get("max_tokens")),
        "extra_params": dict(safe_extra_params),
    }
