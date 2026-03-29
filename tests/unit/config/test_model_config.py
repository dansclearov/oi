from unittest.mock import patch

from llm_cli.model_config import (
    clear_model_capabilities_cache,
    get_model_capabilities,
    load_model_capabilities,
)


def test_load_model_capabilities_caches_yaml_reads():
    merged_config = {
        "openai": {
            "gpt-4o": {
                "supports_search": True,
            }
        },
        "aliases": {"default": "openai/gpt-4o"},
    }

    clear_model_capabilities_cache()
    try:
        with patch(
            "llm_cli.model_config.load_merged_model_config", return_value=merged_config
        ) as mock_load:
            first = load_model_capabilities()
            second = load_model_capabilities()

            assert first == second
            assert "aliases" not in first
            assert mock_load.call_count == 1
    finally:
        clear_model_capabilities_cache()


def test_get_model_capabilities_copies_extra_params():
    clear_model_capabilities_cache()
    try:
        with patch(
            "llm_cli.model_config.load_model_capabilities",
            return_value={
                "provider": {
                    "model": {
                        "extra_params": {"foo": "bar"},
                        "supports_search": True,
                        "max_tokens": 8192,
                    }
                }
            },
        ):
            caps = get_model_capabilities("provider", "model")

            assert caps["supports_search"] is True
            assert caps["max_tokens"] == 8192
            assert caps["extra_params"] == {"foo": "bar"}
            caps["extra_params"]["foo"] = "changed"

            caps_again = get_model_capabilities("provider", "model")
            assert caps_again["extra_params"] == {"foo": "bar"}
    finally:
        clear_model_capabilities_cache()


def test_get_model_capabilities_ignores_invalid_max_tokens():
    clear_model_capabilities_cache()
    try:
        with patch(
            "llm_cli.model_config.load_model_capabilities",
            return_value={
                "provider": {
                    "model": {
                        "max_tokens": 0,
                    }
                }
            },
        ):
            caps = get_model_capabilities("provider", "model")

            assert caps["max_tokens"] is None
    finally:
        clear_model_capabilities_cache()
