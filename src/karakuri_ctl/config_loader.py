"""Configuration file loader with inheritance and variable expansion.

Supports:
- extends: Include base configuration from another file
- merge: Merge external files into namespaces
- ${variable.path}: Variable expansion
- when: Conditional sections (future)
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml


class ConfigLoader:
    """Loads configuration files with inheritance and variable expansion."""

    def __init__(self, base_path: Optional[Path] = None):
        """Initialize the config loader.

        Args:
            base_path: Base directory for resolving relative paths.
        """
        self.base_path = base_path or Path.cwd()
        self._loaded_files: Dict[str, Dict[str, Any]] = {}

    def load(self, path: Union[str, Path]) -> Dict[str, Any]:
        """Load a configuration file with full processing.

        Args:
            path: Path to the configuration file.

        Returns:
            Fully processed configuration dictionary.
        """
        path = Path(path)
        if not path.is_absolute():
            path = self.base_path / path

        # Load raw YAML
        config = self._load_yaml(path)

        # Process extends
        if "extends" in config:
            extends_path = self._resolve_path(config["extends"], path.parent)
            base_config = self.load(extends_path)
            config = self._merge_dicts(base_config, config)
            del config["extends"]

        # Process merge directives
        if "merge" in config:
            for merge_item in config["merge"]:
                source_path = self._resolve_path(merge_item["source"], path.parent)
                namespace = merge_item.get("as", Path(source_path).stem)
                merged_config = self.load(source_path)
                config[namespace] = merged_config
            del config["merge"]

        # Expand variables
        config = self._expand_variables(config, config)

        return config

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        """Load raw YAML file."""
        if str(path) in self._loaded_files:
            return self._loaded_files[str(path)].copy()

        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        self._loaded_files[str(path)] = data
        return data.copy()

    def _resolve_path(self, path_str: str, current_dir: Path) -> Path:
        """Resolve a relative path."""
        path = Path(path_str)
        if path.is_absolute():
            return path
        return (current_dir / path).resolve()

    def _merge_dicts(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deep merge two dictionaries."""
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_dicts(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                # For lists, override completely (don't append)
                result[key] = value
            else:
                result[key] = value

        return result

    def _expand_variables(
        self, obj: Any, context: Dict[str, Any]
    ) -> Any:
        """Recursively expand ${variable.path} references."""
        if isinstance(obj, str):
            return self._expand_string(obj, context)
        elif isinstance(obj, dict):
            return {k: self._expand_variables(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._expand_variables(item, context) for item in obj]
        else:
            return obj

    def _expand_string(self, s: str, context: Dict[str, Any]) -> str:
        """Expand ${variable.path} in a string."""
        pattern = r'\$\{([^}]+)\}'

        def replace(match):
            var_path = match.group(1)

            # Handle default values: ${var:-default}
            if ":-" in var_path:
                var_path, default = var_path.split(":-", 1)
            else:
                default = match.group(0)  # Keep original if not found

            # First try environment variable
            env_val = os.environ.get(var_path)
            if env_val is not None:
                return env_val

            # Then try config path
            value = self._get_nested_value(context, var_path)
            if value is not None:
                return str(value) if not isinstance(value, str) else value

            return default

        return re.sub(pattern, replace, s)

    def _get_nested_value(
        self, obj: Dict[str, Any], path: str
    ) -> Optional[Any]:
        """Get a nested value using dot notation."""
        parts = path.split(".")
        current = obj

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current


class ProfileLoader:
    """Loads profile configurations with the new format."""

    def __init__(self, infrastructure_path: Path):
        """Initialize the profile loader.

        Args:
            infrastructure_path: Path to infrastructure/ directory.
        """
        self.infrastructure_path = infrastructure_path
        self.profiles_path = infrastructure_path / "profiles"
        self.config_loader = ConfigLoader(self.profiles_path)

    def list_profiles(self) -> List[str]:
        """List available profile names."""
        profiles = []
        if self.profiles_path.exists():
            for f in self.profiles_path.glob("*.yaml"):
                # Skip base files
                if f.name.startswith("_"):
                    continue
                profiles.append(f.stem)
            for f in self.profiles_path.glob("*.yml"):
                if f.name.startswith("_"):
                    continue
                profiles.append(f.stem)
        return sorted(profiles)

    def load_profile(self, name: str) -> Dict[str, Any]:
        """Load a profile by name.

        Args:
            name: Profile name (without extension).

        Returns:
            Fully processed profile configuration.
        """
        # Try .yaml first, then .yml
        for ext in [".yaml", ".yml"]:
            path = self.profiles_path / f"{name}{ext}"
            if path.exists():
                return self.config_loader.load(path)

        raise FileNotFoundError(f"Profile '{name}' not found in {self.profiles_path}")

    def get_profile_info(self, name: str) -> Dict[str, Any]:
        """Get basic profile info."""
        config = self.load_profile(name)
        skills = config.get("skills", [])
        skill_names = [s.get("name", "") if isinstance(s, dict) else s for s in skills]

        return {
            "name": config.get("name", name),
            "description": config.get("description", ""),
            "skills": skill_names,
        }
