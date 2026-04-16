from typing import Any

from llm_cli.config.loaders import load_merged_model_config, parse_models_and_aliases
from llm_cli.exceptions import ModelNotFoundError
from llm_cli.llm_types import ModelCapabilities
from llm_cli.ui.labels import WARNING_LABEL, ansi_message

# Aliases to exclude from display models
EXCLUDED_ALIASES = {"default"}


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


class ModelRegistry:
    """Registry for managing model aliases and metadata."""

    def __init__(self):
        merged_config = load_merged_model_config()
        self._model_map: dict[str, tuple[str, str]] = {}
        self._aliases: dict[str, tuple[str, str]] = {}
        self._default_model: str = ""
        self._provider_configs: dict[str, dict[str, Any]] = {}

        self._model_map, self._default_model = parse_models_and_aliases(merged_config)
        self._aliases = {
            alias: mapping
            for alias, mapping in self._model_map.items()
            if alias != mapping[1]
        }
        self._provider_configs = {
            provider: models
            for provider, models in merged_config.items()
            if provider != "aliases"
            and not provider.startswith("_")
            and isinstance(models, dict)
        }

    def get_provider_for_model(self, model_name_or_alias: str) -> tuple[str, str]:
        """Get provider/model for an alias or a resolved `provider:model-id` name."""
        if model_name_or_alias in self._model_map:
            return self._model_map[model_name_or_alias]

        parsed = self._parse_model_name(model_name_or_alias)
        if parsed is not None:
            return parsed

        available_models = list(self._model_map.keys())
        raise ModelNotFoundError(
            "Unknown model: "
            f"{model_name_or_alias}. Available models: {available_models}"
        )

    def resolve_model_name(self, model_name_or_alias: str) -> str:
        """Return a canonical pydantic-ai model name (e.g. `provider:model-id`)."""
        provider_name, model_id = self.get_provider_for_model(model_name_or_alias)
        return f"{provider_name}:{model_id}"

    def get_available_models(self) -> dict[str, str]:
        """Get the raw provider/model pairs for each alias."""
        return {
            alias: f"{provider_name}:{model_id}"
            for alias, (provider_name, model_id) in self._model_map.items()
        }

    def get_default_model(self) -> str:
        """Get the default model alias."""
        return self._default_model

    def get_model_capabilities(self, model_name_or_alias: str) -> ModelCapabilities:
        """Get capabilities for a specific alias or resolved model name."""
        provider_name, model_id = self.get_provider_for_model(model_name_or_alias)

        model_entry = self._provider_configs.get(provider_name, {}).get(model_id, {})
        model_config = model_entry if isinstance(model_entry, dict) else {}
        extra_params = model_config.get("extra_params", {})
        safe_extra_params = dict(extra_params) if isinstance(extra_params, dict) else {}

        return ModelCapabilities(
            supports_search=bool(model_config.get("supports_search", False)),
            supports_thinking=bool(model_config.get("supports_thinking", False)),
            supports_vision=bool(model_config.get("supports_vision", False)),
            max_tokens=_normalize_max_tokens(model_config.get("max_tokens")),
            extra_params=safe_extra_params,
        )

    def has_model_config(self, model_name_or_alias: str) -> bool:
        """Return True when the model has an explicit entry in merged models config."""
        provider_name, model_id = self.get_provider_for_model(model_name_or_alias)
        provider_models = self._provider_configs.get(provider_name, {})
        return model_id in provider_models

    def get_display_models(self) -> list[str]:
        """Get models for CLI display, preferring aliases over full names."""
        alias_targets = {
            alias: target
            for alias, target in self._aliases.items()
            if alias not in EXCLUDED_ALIASES
        }

        display_models = set(alias_targets.keys())

        alias_target_values = set(alias_targets.values())
        for model_alias, mapping in self._model_map.items():
            if model_alias == mapping[1] and mapping not in alias_target_values:
                display_models.add(model_alias)

        if self._default_model in self._model_map:
            display_models.add(self._default_model)

        return sorted(display_models)

    def _parse_model_name(self, model_name: str) -> tuple[str, str] | None:
        """Parse a resolved `provider:model-id` string."""
        if ":" not in model_name:
            return None

        provider_name, model_id = model_name.split(":", 1)
        if not provider_name or not model_id:
            return None

        return provider_name, model_id
