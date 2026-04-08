from unittest.mock import patch

import pytest

from llm_cli.exceptions import ModelNotFoundError
from llm_cli.registry import ModelRegistry


class TestModelRegistry:
    def test_get_provider_for_model_success(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = (
                {"test-model": ("test", "test-model")},
                "test-model",
            )

            registry = ModelRegistry()
            provider, model_id = registry.get_provider_for_model("test-model")

            assert provider == "test"
            assert model_id == "test-model"

    def test_get_provider_for_model_accepts_resolved_model_name(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )

            registry = ModelRegistry()
            provider, model_id = registry.get_provider_for_model("provider:model")

            assert provider == "provider"
            assert model_id == "model"

    def test_get_provider_for_model_not_found(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = ({}, "default")

            registry = ModelRegistry()

            with pytest.raises(ModelNotFoundError) as exc_info:
                registry.get_provider_for_model("nonexistent")

            assert "Unknown model: nonexistent" in str(exc_info.value)

    def test_resolve_model_name(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )

            registry = ModelRegistry()
            assert registry.resolve_model_name("alias") == "provider:model"

    def test_resolve_model_name_passes_through_resolved_model_name(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )

            registry = ModelRegistry()
            assert registry.resolve_model_name("provider:model") == "provider:model"

    def test_get_available_models(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = (
                {"model1": ("provider1", "model1"), "model2": ("provider2", "model2")},
                "model1",
            )

            registry = ModelRegistry()
            models = registry.get_available_models()

            assert models == {
                "model1": "provider1:model1",
                "model2": "provider2:model2",
            }

    def test_get_default_model(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = ({}, "test-default")

            registry = ModelRegistry()
            assert registry.get_default_model() == "test-default"

    def test_get_model_capabilities(self):
        with (
            patch("llm_cli.registry.load_models_and_aliases") as mock_load,
            patch("llm_cli.registry.get_model_capabilities") as mock_caps,
        ):
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )
            mock_caps.return_value = {"supports_search": True}

            registry = ModelRegistry()
            capabilities = registry.get_model_capabilities("alias")
            assert capabilities.supports_search is True
            assert capabilities.supports_thinking is False
            assert capabilities.max_tokens is None
            mock_caps.assert_called_once_with("provider", "model")

    def test_get_model_capabilities_accepts_resolved_model_name(self):
        with (
            patch("llm_cli.registry.load_models_and_aliases") as mock_load,
            patch("llm_cli.registry.get_model_capabilities") as mock_caps,
        ):
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )
            mock_caps.return_value = {"supports_thinking": True, "max_tokens": 16384}

            registry = ModelRegistry()
            capabilities = registry.get_model_capabilities("provider:model")
            assert capabilities.supports_search is False
            assert capabilities.supports_thinking is True
            assert capabilities.max_tokens == 16384
            mock_caps.assert_called_once_with("provider", "model")

    def test_has_model_config_returns_true_for_configured_model(self):
        with (
            patch("llm_cli.registry.load_models_and_aliases") as mock_load,
            patch("llm_cli.registry.load_model_capabilities") as mock_caps_map,
        ):
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )
            mock_caps_map.return_value = {"provider": {"model": {}}}

            registry = ModelRegistry()
            assert registry.has_model_config("alias") is True

    def test_has_model_config_returns_false_for_missing_model(self):
        with (
            patch("llm_cli.registry.load_models_and_aliases") as mock_load,
            patch("llm_cli.registry.load_model_capabilities") as mock_caps_map,
        ):
            mock_load.return_value = (
                {"alias": ("provider", "model")},
                "alias",
            )
            mock_caps_map.return_value = {"provider": {"other": {}}}

            registry = ModelRegistry()
            assert registry.has_model_config("alias") is False

    def test_get_display_models_includes_default_model(self):
        with patch("llm_cli.registry.load_models_and_aliases") as mock_load:
            mock_load.return_value = (
                {
                    "gpt-4o": ("openai", "gpt-4o"),
                    "fast": ("openai", "gpt-4o"),
                },
                "gpt-4o",
            )

            registry = ModelRegistry()
            models = registry.get_display_models()

            assert "gpt-4o" in models
