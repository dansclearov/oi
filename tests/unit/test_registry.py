from unittest.mock import patch

import pytest

from oi.exceptions import ModelNotFoundError
from oi.registry import ModelRegistry


def _mock_config(providers: dict, aliases: dict | None = None):
    """Build a minimal merged config for testing."""
    config = dict(providers)
    if aliases is not None:
        config["aliases"] = aliases
    elif "aliases" not in config:
        # Auto-generate a default alias from the first model
        for provider, models in providers.items():
            for model_id in models:
                config["aliases"] = {"default": f"{provider}/{model_id}"}
                return config
    return config


class TestModelRegistry:
    def test_get_provider_for_model_success(self):
        config = _mock_config({"test": {"test-model": {}}})
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            provider, model_id = registry.get_provider_for_model("test-model")

            assert provider == "test"
            assert model_id == "test-model"

    def test_get_provider_for_model_accepts_resolved_model_name(self):
        config = _mock_config(
            {"provider": {"model": {}}},
            aliases={"default": "provider/model", "alias": "provider/model"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            provider, model_id = registry.get_provider_for_model("provider:model")

            assert provider == "provider"
            assert model_id == "model"

    def test_get_provider_for_model_not_found(self):
        config = _mock_config({"p": {"m": {}}})
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()

            with pytest.raises(ModelNotFoundError) as exc_info:
                registry.get_provider_for_model("nonexistent")

            assert "Unknown model: nonexistent" in str(exc_info.value)

    def test_resolve_model_name(self):
        config = _mock_config(
            {"provider": {"model": {}}},
            aliases={"default": "provider/model", "alias": "provider/model"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            assert registry.resolve_model_name("alias") == "provider:model"

    def test_resolve_model_name_passes_through_resolved_model_name(self):
        config = _mock_config(
            {"provider": {"model": {}}},
            aliases={"default": "provider/model", "alias": "provider/model"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            assert registry.resolve_model_name("provider:model") == "provider:model"

    def test_get_available_models(self):
        config = _mock_config(
            {"provider1": {"model1": {}}, "provider2": {"model2": {}}},
            aliases={"default": "provider1/model1"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            models = registry.get_available_models()

            assert models == {
                "model1": "provider1:model1",
                "model2": "provider2:model2",
            }

    def test_get_default_model(self):
        config = _mock_config(
            {"test": {"test-default": {}}},
            aliases={"default": "test/test-default"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            assert registry.get_default_model() == "test-default"

    def test_get_model_capabilities(self):
        config = _mock_config(
            {"provider": {"model": {"supports_search": True}}},
            aliases={"default": "provider/model", "alias": "provider/model"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            capabilities = registry.get_model_capabilities("alias")
            assert capabilities.supports_search is True
            assert capabilities.supports_thinking is False
            assert capabilities.max_tokens is None

    def test_get_model_capabilities_accepts_resolved_model_name(self):
        config = _mock_config(
            {"provider": {"model": {"supports_thinking": True, "max_tokens": 16384}}},
            aliases={"default": "provider/model", "alias": "provider/model"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            capabilities = registry.get_model_capabilities("provider:model")
            assert capabilities.supports_search is False
            assert capabilities.supports_thinking is True
            assert capabilities.max_tokens == 16384

    def test_get_model_capabilities_copies_extra_params(self):
        config = _mock_config(
            {
                "provider": {
                    "model": {
                        "extra_params": {"foo": "bar"},
                        "supports_search": True,
                        "max_tokens": 8192,
                    }
                }
            },
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            caps = registry.get_model_capabilities("model")

            assert caps.supports_search is True
            assert caps.max_tokens == 8192
            assert caps.extra_params == {"foo": "bar"}
            caps.extra_params["foo"] = "changed"

            caps_again = registry.get_model_capabilities("model")
            assert caps_again.extra_params == {"foo": "bar"}

    def test_get_model_capabilities_ignores_invalid_max_tokens(self):
        config = _mock_config({"provider": {"model": {"max_tokens": 0}}})
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            caps = registry.get_model_capabilities("model")
            assert caps.max_tokens is None

    def test_has_model_config_returns_true_for_configured_model(self):
        config = _mock_config(
            {"provider": {"model": {}}},
            aliases={"default": "provider/model", "alias": "provider/model"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            assert registry.has_model_config("alias") is True

    def test_has_model_config_returns_false_for_missing_model(self):
        config = _mock_config(
            {"provider": {"other": {}}},
            aliases={"default": "provider/other", "alias": "provider/other"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            # Use provider:model syntax to bypass model map
            assert registry.has_model_config("provider:model") is False

    def test_get_display_models_includes_default_model(self):
        config = _mock_config(
            {"openai": {"gpt-4o": {}}},
            aliases={"default": "openai/gpt-4o", "fast": "openai/gpt-4o"},
        )
        with patch("oi.registry.load_merged_model_config", return_value=config):
            registry = ModelRegistry()
            models = registry.get_display_models()

            assert "gpt-4o" in models
