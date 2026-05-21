from oi.config.settings import Config
from oi.core.client import LLMClient
from oi.registry import ModelRegistry


# Test Config
def test_config_defaults():
    config = Config()
    assert isinstance(config.chat_dir, str)


# Test LLMClient
def test_llm_client_init():
    registry = ModelRegistry()
    client = LLMClient(registry)
    assert client.registry is not None
    assert client.registry == registry
