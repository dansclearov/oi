import re
from importlib import resources
from pathlib import Path

from platformdirs import user_config_dir

from oi.exceptions import PromptNotFoundError


def read_system_message_from_file(file_name: str) -> str:
    """Read system message from a prompt file, checking user config first then package."""
    # First try user config directory
    config_dir = Path(user_config_dir("oi", ensure_exists=True)) / "prompts"
    config_dir.mkdir(exist_ok=True)
    user_prompt = config_dir / file_name

    if user_prompt.exists():
        with open(user_prompt, "r") as file:
            return file.read()

    # Fall back to package prompts
    try:
        with (
            resources.files("oi")
            .joinpath("prompts")
            .joinpath(file_name)
            .open("r") as file
        ):
            return file.read()
    except FileNotFoundError:
        raise PromptNotFoundError(
            f"Prompt file {file_name} not found in either "
            f"{config_dir} or package prompts"
        )


def get_prompts() -> list[str]:
    """Get available prompts from both user config and package directories."""
    prompts = set()  # Use set to avoid duplicates
    pattern = r"prompt_(.+)\.txt"

    # Check user config directory
    config_dir = Path(user_config_dir("oi", ensure_exists=True)) / "prompts"
    config_dir.mkdir(exist_ok=True)

    # Add prompts from user config
    for file in config_dir.glob("prompt_*.txt"):
        if match := re.match(pattern, file.name):
            prompts.add(match.group(1))

    # Add prompts from package
    try:
        for file in resources.files("oi").joinpath("prompts").iterdir():
            if match := re.match(pattern, file.name):
                prompts.add(match.group(1))
    except (TypeError, ModuleNotFoundError):
        pass  # Handle case where package prompts directory doesn't exist

    return sorted(list(prompts))  # Return sorted list for consistent ordering
