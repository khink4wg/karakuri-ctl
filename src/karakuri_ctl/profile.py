"""Profile management for karakuri-ctl."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class ServiceConfig:
    """Configuration for a single service."""
    name: str
    depends_on: List[str] = field(default_factory=list)
    wait_for_healthy: bool = False
    environment: Dict[str, str] = field(default_factory=dict)
    command: Optional[str] = None


@dataclass
class Profile:
    """Profile definition for a deployment configuration."""
    name: str
    description: str = ""
    compose_files: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    services: List[ServiceConfig] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "Profile":
        """Load profile from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        services = []
        for svc in data.get("services", []):
            if isinstance(svc, str):
                services.append(ServiceConfig(name=svc))
            else:
                services.append(ServiceConfig(
                    name=svc["name"],
                    depends_on=svc.get("depends_on", []),
                    wait_for_healthy=svc.get("wait_for_healthy", False),
                    environment=svc.get("environment", {}),
                    command=svc.get("command"),
                ))

        # Parse compose_files - can be string or list
        compose_files_raw = data.get("compose_files", [])
        if isinstance(compose_files_raw, str):
            compose_files = [compose_files_raw]
        else:
            compose_files = list(compose_files_raw)

        return cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            compose_files=compose_files,
            environment=data.get("environment", {}),
            services=services,
        )

    def get_ordered_services(self) -> List[ServiceConfig]:
        """Return services in dependency order (topological sort)."""
        # Build dependency graph
        graph: Dict[str, List[str]] = {svc.name: svc.depends_on for svc in self.services}
        service_map = {svc.name: svc for svc in self.services}

        # Topological sort
        visited = set()
        result = []

        def visit(name: str):
            if name in visited:
                return
            visited.add(name)
            for dep in graph.get(name, []):
                if dep in graph:
                    visit(dep)
            if name in service_map:
                result.append(service_map[name])

        for svc in self.services:
            visit(svc.name)

        return result


class ProfileManager:
    """Manages profile discovery and loading."""

    def __init__(self, profiles_dir: Path):
        self.profiles_dir = profiles_dir

    def list_profiles(self) -> List[str]:
        """List available profile names."""
        profiles = []
        if self.profiles_dir.exists():
            for f in self.profiles_dir.glob("*.yaml"):
                profiles.append(f.stem)
            for f in self.profiles_dir.glob("*.yml"):
                profiles.append(f.stem)
        return sorted(profiles)

    def load_profile(self, name: str) -> Profile:
        """Load a profile by name."""
        # Try .yaml first, then .yml
        for ext in [".yaml", ".yml"]:
            path = self.profiles_dir / f"{name}{ext}"
            if path.exists():
                return Profile.from_yaml(path)

        raise FileNotFoundError(f"Profile '{name}' not found in {self.profiles_dir}")

    def get_profile_info(self, name: str) -> Dict[str, Any]:
        """Get profile info without full loading."""
        profile = self.load_profile(name)
        return {
            "name": profile.name,
            "description": profile.description,
            "services": [svc.name for svc in profile.services],
        }
