"""Configuration loader utilities."""

import os
import re
import yaml
from dotenv import load_dotenv


def _substitute_env_vars(config_dict):
    """
    Recursively substitute environment variables in config values.
    Supports ${VAR_NAME:-default_value} syntax.
    """
    if isinstance(config_dict, dict):
        return {key: _substitute_env_vars(value) for key, value in config_dict.items()}
    elif isinstance(config_dict, list):
        return [_substitute_env_vars(item) for item in config_dict]
    elif isinstance(config_dict, str):
        # Match ${VAR_NAME} or ${VAR_NAME:-default}
        pattern = r"\$\{([^}]+)\}"

        def replace_env_var(match):
            var_expr = match.group(1)
            if ":-" in var_expr:
                var_name, default_value = var_expr.split(":-", 1)
                return os.getenv(var_name.strip(), default_value)
            else:
                return os.getenv(var_expr, "")

        return re.sub(pattern, replace_env_var, config_dict)
    else:
        return config_dict


def load_pipeline_config():
    """
    Load the pipeline YAML configuration with environment variable substitution.

    Returns:
        dict: Parsed YAML configuration with environment variables resolved.

    Raises:
        FileNotFoundError: If the config file is missing.
        yaml.YAMLError: If the YAML cannot be parsed.
    """
    # Ensure environment variables are available for downstream modules
    try:
        load_dotenv()
    except Exception as e:
        # Log the specific error but don't fail - .env file might not exist in production
        import logging

        logging.getLogger(__name__).debug(f"Could not load .env file: {e}")

    base_dir = os.path.dirname(os.path.dirname(__file__))  # Go up from utils
    config_path = os.path.join(base_dir, "config_manager", "config_pipeline.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Substitute environment variables
    return _substitute_env_vars(config)
