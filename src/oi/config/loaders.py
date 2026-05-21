"""Configuration loaders for model registry."""

import copy
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

from oi.constants import DEFAULT_FALLBACK_MODEL
from oi.exceptions import ConfigurationError


def _ensure_user_config() -> Path:
    """Ensure user config directory exists and create default models.yaml if missing."""
    config_dir = Path(user_config_dir("oi"))
    config_dir.mkdir(parents=True, exist_ok=True)

    user_config_path = config_dir / "models.yaml"
    if not user_config_path.exists():
        # Copy template to user config
        try:
            template_content = (
                resources.files("oi").joinpath("models_template.yaml").read_text()
            )
            user_config_path.write_text(template_content)
        except Exception:
            # Non-fatal: user can still use built-in models
            pass

    return user_config_path


def _deep_merge_models_section(
    package_section: dict[str, Any], user_section: dict[str, Any]
) -> dict[str, Any]:
    """Deep-merge model configs inside one provider section."""
    merged_section = copy.deepcopy(package_section)

    for model_id, model_config in user_section.items():
        package_model_config = merged_section.get(model_id)
        if isinstance(package_model_config, dict) and isinstance(model_config, dict):
            package_model_config.update(model_config)
        else:
            merged_section[model_id] = copy.deepcopy(model_config)

    return merged_section


def _merge_model_configs(
    package_config: dict[str, Any], user_config: dict[str, Any]
) -> dict[str, Any]:
    """Merge package + user model configs with alias override semantics."""
    merged_config = copy.deepcopy(package_config)

    for section_name, section_data in user_config.items():
        if section_name.startswith("_"):
            continue

        if section_name == "aliases":
            if isinstance(section_data, dict):
                package_aliases = merged_config.get("aliases")
                if not isinstance(package_aliases, dict):
                    package_aliases = {}
                package_aliases.update(section_data)
                merged_config["aliases"] = package_aliases
            continue

        if not isinstance(section_data, dict):
            merged_config[section_name] = copy.deepcopy(section_data)
            continue

        package_section = merged_config.get(section_name)
        if isinstance(package_section, dict):
            merged_config[section_name] = _deep_merge_models_section(
                package_section, section_data
            )
        else:
            merged_config[section_name] = copy.deepcopy(section_data)

    return merged_config


def load_merged_model_config() -> dict[str, Any]:
    """Load and merge package + user models.yaml configuration."""
    try:
        with resources.files("oi").joinpath("models.yaml").open("r") as f:
            package_config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise ConfigurationError("models.yaml not found")
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML in models.yaml: {e}")
    except Exception as e:
        raise ConfigurationError(f"Error loading models from models.yaml: {e}")

    if not isinstance(package_config, dict):
        raise ConfigurationError("Invalid models.yaml: top-level mapping required")

    user_config_path = _ensure_user_config()
    user_config: dict[str, Any] = {}
    if user_config_path.exists():
        try:
            with open(user_config_path, "r") as f:
                loaded_user_config = yaml.safe_load(f) or {}
                if not isinstance(loaded_user_config, dict):
                    raise ConfigurationError(
                        "Invalid user models.yaml: top-level mapping required"
                    )
                user_config = loaded_user_config
        except ConfigurationError:
            raise
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in user models.yaml: {e}")
        except Exception as e:
            raise ConfigurationError(f"Error loading user models.yaml: {e}")

    return _merge_model_configs(package_config, user_config)


def parse_models_and_aliases(
    merged_config: dict[str, Any],
) -> tuple[dict[str, tuple[str, str]], str]:
    """Parse model map and default alias from a pre-merged config dict.

    Returns:
        Tuple of (model_map, default_model)
    """
    model_map: dict[str, tuple[str, str]] = {}
    default_model = DEFAULT_FALLBACK_MODEL  # fallback default

    # Load all models from all provider sections dynamically
    for section_name, section_data in merged_config.items():
        if section_name == "aliases":
            continue

        # Skip top-level keys starting with _ (YAML anchors, metadata, etc.)
        if section_name.startswith("_"):
            continue

        # Each top-level section (except aliases) is a provider
        if isinstance(section_data, dict):
            for model_id in section_data.keys():
                existing_mapping = model_map.get(model_id)
                if existing_mapping and existing_mapping[0] != section_name:
                    raise ConfigurationError(
                        "Duplicate model ID "
                        f"'{model_id}' found in providers '{existing_mapping[0]}' "
                        f"and '{section_name}'"
                    )
                # Register model ID as direct alias
                model_map[model_id] = (section_name, model_id)

    # Load aliases
    aliases_raw = merged_config.get("aliases", {})
    if aliases_raw is None:
        aliases: dict[str, Any] = {}
    elif isinstance(aliases_raw, dict):
        aliases = aliases_raw
    else:
        raise ConfigurationError("Invalid models.yaml: aliases must be a mapping")

    # Set default model
    default_spec = aliases.get("default")
    default_defined_in_config = default_spec is not None
    if default_spec is not None and not isinstance(default_spec, str):
        raise ConfigurationError(
            "Invalid models.yaml: aliases.default must be a string"
        )
    if isinstance(default_spec, str):
        if "/" in default_spec:
            provider_name, model_id = default_spec.split("/", 1)
            if not provider_name or not model_id:
                raise ConfigurationError(
                    f"Invalid default alias target format: {default_spec}"
                )
            default_model = model_id
        else:
            default_model = default_spec

    # Load all aliases
    unresolved_aliases: list[tuple[str, str]] = []
    for alias, model_spec in aliases.items():
        if alias == "default":
            continue

        # Ignore malformed alias entries, but keep loading valid ones.
        if not isinstance(alias, str) or not isinstance(model_spec, str):
            continue

        if "/" in model_spec:
            provider_name, model_id = model_spec.split("/", 1)
            if not provider_name or not model_id:
                continue
            model_map[alias] = (provider_name, model_id)
        else:
            unresolved_aliases.append((alias, model_spec))

    # Resolve aliases that point at other aliases/model IDs (without provider prefix).
    # We iterate to support alias chains.
    while unresolved_aliases:
        next_unresolved: list[tuple[str, str]] = []
        made_progress = False
        for alias, model_spec in unresolved_aliases:
            mapping = model_map.get(model_spec)
            if mapping is None:
                next_unresolved.append((alias, model_spec))
                continue
            model_map[alias] = mapping
            made_progress = True

        if not made_progress:
            break
        unresolved_aliases = next_unresolved

    if not model_map:
        raise ConfigurationError("No models found in merged models configuration")

    if default_model not in model_map:
        if default_defined_in_config:
            raise ConfigurationError(
                f"Default model '{default_model}' does not resolve to a configured model"
            )
        default_model = next(iter(model_map))

    return model_map, default_model


def load_models_and_aliases() -> tuple[dict[str, tuple[str, str]], str]:
    """Load config from disk and parse into model map + default (convenience wrapper)."""
    return parse_models_and_aliases(load_merged_model_config())
