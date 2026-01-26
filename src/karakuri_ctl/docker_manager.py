"""Docker Compose management for karakuri-ctl."""

import json
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# Support both package and direct execution
try:
    from .profile import Profile, ServiceConfig
except ImportError:
    from profile import Profile, ServiceConfig


def load_env_file(path: Path) -> Dict[str, str]:
    """Load environment variables from a file.

    Supports basic .env format:
    - KEY=value
    - # comments
    - empty lines
    """
    env = {}
    if not path.exists():
        return env

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            # Parse KEY=value
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                env[key] = value
    return env


class ServiceState(str, Enum):
    """Container state."""
    RUNNING = "running"
    EXITED = "exited"
    CREATED = "created"
    RESTARTING = "restarting"
    PAUSED = "paused"
    DEAD = "dead"
    NOT_FOUND = "not_found"


@dataclass
class ServiceStatus:
    """Status of a service."""
    name: str
    state: ServiceState
    container_id: Optional[str] = None
    health: Optional[str] = None
    ports: Optional[str] = None


class DockerManager:
    """Manages Docker Compose operations."""

    def __init__(self, project_root: Path, project_name: str = "ws"):
        self.project_root = project_root
        self.project_name = project_name
        # Default compose file
        self.default_compose_file = project_root / "docker-compose.yml"
        if not self.default_compose_file.exists():
            self.default_compose_file = project_root / "docker-compose.yaml"
        # Config directory for external settings
        self.config_dir = project_root / "config"
        # Infrastructure directory
        self.infrastructure_dir = project_root / "infrastructure"
        # Source directory (skills are in src/<skill_name>/)
        self.src_dir = project_root / "src"
        # Skills catalog (loaded from infrastructure/skills.yaml)
        self._skills_catalog: Optional[Dict] = None

    def _load_skills_catalog(self) -> Dict:
        """Load skills catalog from infrastructure/skills.yaml.

        Returns:
            Dict with 'skills' key containing tier -> skill list mapping
        """
        if self._skills_catalog is not None:
            return self._skills_catalog

        skills_file = self.infrastructure_dir / "skills.yaml"
        if not skills_file.exists():
            self._skills_catalog = {"skills": {}}
            return self._skills_catalog

        with open(skills_file, "r") as f:
            self._skills_catalog = yaml.safe_load(f) or {"skills": {}}
        return self._skills_catalog

    def get_skill_tier(self, skill_name: str) -> Optional[str]:
        """Get the tier for a skill from the skills catalog.

        Args:
            skill_name: Name of the skill

        Returns:
            Tier name (e.g., 'low_level', 'high_level', 'execution') or None if not found
        """
        catalog = self._load_skills_catalog()
        skills = catalog.get("skills", {})
        for tier, skill_list in skills.items():
            if skill_name in skill_list:
                return tier
        return None

    def get_all_skills(self) -> Dict[str, List[str]]:
        """Get all skills grouped by tier.

        Returns:
            Dict mapping tier name to list of skill names
        """
        catalog = self._load_skills_catalog()
        return catalog.get("skills", {})

    def get_skill_compose_file(self, skill_name: str, tier: str = "low_level") -> Optional[Path]:
        """Get the docker-compose.yml path for a skill.

        Skills are located at:
        - src/<skill_name>/docker-compose.yml
        """
        skill_path = self.src_dir / skill_name / "docker-compose.yml"
        if skill_path.exists():
            return skill_path
        return None

    def load_external_config(self) -> Dict[str, str]:
        """Load external configuration from config/*.env files.

        Currently loads:
        - config/ads.env: ADS connection settings
        """
        env = {}
        # Load ADS config if exists
        ads_env_file = self.config_dir / "ads.env"
        if ads_env_file.exists():
            ads_env = load_env_file(ads_env_file)
            env.update(ads_env)
        return env

    def _build_compose_cmd(self, compose_files: Optional[List[str]] = None) -> List[str]:
        """Build base docker compose command with file arguments."""
        cmd = ["docker", "compose"]

        # Add compose file arguments
        files_to_use = []
        if compose_files:
            for f in compose_files:
                path = self.project_root / f
                if path.exists():
                    files_to_use.append(path)
                else:
                    print(f"  Warning: compose file not found: {f}")

        # Always include default if no files specified or as base
        if not files_to_use:
            files_to_use = [self.default_compose_file]

        for f in files_to_use:
            cmd.extend(["-f", str(f)])

        cmd.extend(["-p", self.project_name])
        return cmd

    def _run_compose(
        self,
        args: List[str],
        compose_files: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run docker compose command."""
        cmd = self._build_compose_cmd(compose_files)
        cmd.extend(args)

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        return subprocess.run(
            cmd,
            cwd=self.project_root,
            env=run_env,
            capture_output=capture_output,
            text=True,
            check=check,
        )

    def get_service_status(self, service_name: str, compose_files: Optional[List[str]] = None) -> ServiceStatus:
        """Get status of a single service."""
        try:
            result = self._run_compose(
                ["ps", "--format", "json", service_name],
                compose_files=compose_files,
                capture_output=True,
                check=False,
            )

            if result.returncode != 0 or not result.stdout.strip():
                return ServiceStatus(name=service_name, state=ServiceState.NOT_FOUND)

            # Parse JSON output (one line per container)
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                state_str = data.get("State", "").lower()
                try:
                    state = ServiceState(state_str)
                except ValueError:
                    state = ServiceState.NOT_FOUND

                return ServiceStatus(
                    name=service_name,
                    state=state,
                    container_id=data.get("ID"),
                    health=data.get("Health"),
                    ports=data.get("Publishers"),
                )

            return ServiceStatus(name=service_name, state=ServiceState.NOT_FOUND)

        except Exception:
            return ServiceStatus(name=service_name, state=ServiceState.NOT_FOUND)

    def get_all_status(self, compose_files: Optional[List[str]] = None) -> List[ServiceStatus]:
        """Get status of all services including skill-based containers."""
        statuses = []

        # Get status from default compose file
        try:
            result = self._run_compose(
                ["ps", "--format", "json", "-a"],
                compose_files=compose_files,
                capture_output=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    data = json.loads(line)
                    state_str = data.get("State", "").lower()
                    try:
                        state = ServiceState(state_str)
                    except ValueError:
                        state = ServiceState.NOT_FOUND

                    statuses.append(ServiceStatus(
                        name=data.get("Service", "unknown"),
                        state=state,
                        container_id=data.get("ID"),
                        health=data.get("Health"),
                    ))
        except Exception:
            pass

        # Get status from skill-based containers
        skill_statuses = self._get_skill_statuses()
        statuses.extend(skill_statuses)

        return statuses

    def _get_skill_statuses(self) -> List[ServiceStatus]:
        """Get status of all skill-based containers."""
        statuses = []

        # Load environment for compose files
        run_env = os.environ.copy()
        env_file = self.project_root / ".env"
        if env_file.exists():
            run_env.update(load_env_file(env_file))
        if "HOST_PROJECT_ROOT" not in run_env:
            run_env["HOST_PROJECT_ROOT"] = str(self.project_root)

        # Get skills from catalog (infrastructure/skills.yaml)
        all_skills = self.get_all_skills()
        skill_compose_files = []
        for tier, skill_list in all_skills.items():
            for skill_name in skill_list:
                compose_file = self.src_dir / skill_name / "docker-compose.yml"
                if compose_file.exists():
                    skill_compose_files.append((skill_name, tier, compose_file))

        # Get status for each skill
        for skill_name, tier, compose_file in skill_compose_files:
            try:
                result = subprocess.run(
                    ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json", "-a"],
                    capture_output=True,
                    text=True,
                    cwd=compose_file.parent,
                    env=run_env,
                    check=False,
                )

                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().split("\n"):
                        if not line:
                            continue
                        data = json.loads(line)
                        state_str = data.get("State", "").lower()
                        try:
                            state = ServiceState(state_str)
                        except ValueError:
                            state = ServiceState.NOT_FOUND

                        # Include skill name in service name for clarity
                        service_name = data.get("Service", "unknown")
                        statuses.append(ServiceStatus(
                            name=f"{skill_name}/{service_name}",
                            state=state,
                            container_id=data.get("ID"),
                            health=data.get("Health"),
                        ))
            except Exception:
                pass

        return statuses

    def start_service(
        self,
        service: ServiceConfig,
        compose_files: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        detach: bool = True,
        wait: bool = False,
    ) -> bool:
        """Start a single service."""
        args = ["up"]
        if detach:
            args.append("-d")
        if wait:
            args.append("--wait")
        args.append(service.name)

        try:
            self._run_compose(args, compose_files=compose_files, env=env)
            return True
        except subprocess.CalledProcessError:
            return False

    def stop_service(self, service_name: str, compose_files: Optional[List[str]] = None) -> bool:
        """Stop a single service."""
        try:
            self._run_compose(["stop", service_name], compose_files=compose_files)
            return True
        except subprocess.CalledProcessError:
            return False

    def start_profile(
        self,
        profile: Profile,
        verbose: bool = False,
    ) -> bool:
        """Start all services in a profile."""
        # Load external config first (e.g., config/ads.env)
        env = self.load_external_config()
        # Profile environment overrides external config
        env.update(profile.environment)

        # Get ordered services
        ordered = profile.get_ordered_services()

        # Get compose files from profile
        compose_files = profile.compose_files if profile.compose_files else None

        print(f"Starting profile: {profile.name}")
        if compose_files:
            print(f"  Compose files: {compose_files}")
        print(f"  Environment: {env}")
        print(f"  Services: {[s.name for s in ordered]}")
        print()

        for svc in ordered:
            # Merge service-specific environment
            svc_env = {**env, **svc.environment}

            print(f"  Starting {svc.name}...", end=" ", flush=True)

            success = self.start_service(
                svc,
                compose_files=compose_files,
                env=svc_env,
                detach=True,
                wait=svc.wait_for_healthy,
            )

            if not success:
                print("FAILED")
                return False

            # Wait for container to be running
            for _ in range(30):
                status = self.get_service_status(svc.name, compose_files=compose_files)
                if status.state == ServiceState.RUNNING:
                    print("OK")
                    break
                time.sleep(1)
            else:
                print(f"TIMEOUT (state: {status.state})")
                if not svc.wait_for_healthy:
                    # Continue anyway if not waiting for health
                    pass

            # Add delay between services for startup
            if svc.depends_on:
                time.sleep(2)

        print()
        print("Profile started successfully.")
        return True

    def stop_profile(self, profile: Profile) -> bool:
        """Stop all services in a profile (reverse order)."""
        ordered = profile.get_ordered_services()
        compose_files = profile.compose_files if profile.compose_files else None

        print(f"Stopping profile: {profile.name}")

        # Stop in reverse order
        for svc in reversed(ordered):
            print(f"  Stopping {svc.name}...", end=" ", flush=True)
            success = self.stop_service(svc.name, compose_files=compose_files)
            print("OK" if success else "FAILED")

        return True

    def stop_all(self, compose_files: Optional[List[str]] = None) -> bool:
        """Stop all running containers."""
        try:
            self._run_compose(["down"], compose_files=compose_files)
            return True
        except subprocess.CalledProcessError:
            return False

    def logs(
        self,
        service_name: str,
        compose_files: Optional[List[str]] = None,
        follow: bool = False,
        tail: int = 100,
    ) -> None:
        """Show logs for a service."""
        args = ["logs"]
        if follow:
            args.append("-f")
        args.extend(["--tail", str(tail)])
        args.append(service_name)

        self._run_compose(args, compose_files=compose_files, check=False)

    def start_skill(
        self,
        skill_name: str,
        tier: str = "low_level",
        env: Optional[Dict[str, str]] = None,
        profile: Optional[str] = None,
        wait: bool = True,
    ) -> bool:
        """Start a skill using its own docker-compose.yml.

        Args:
            skill_name: Name of the skill (e.g., 'ros2_control_skill')
            tier: Skill tier ('low_level' or 'high_level')
            env: Environment variables to pass
            profile: Docker Compose profile to use (e.g., 'ads')
            wait: Wait for the service to be healthy

        Returns:
            True if successful, False otherwise
        """
        compose_file = self.get_skill_compose_file(skill_name, tier)
        if not compose_file:
            print(f"Error: docker-compose.yml not found for skill '{skill_name}'")
            return False

        skill_dir = compose_file.parent
        print(f"Starting skill: {skill_name}")
        print(f"  Compose file: {compose_file}")
        if env:
            print(f"  Environment: {list(env.keys())}")

        # Build docker compose command
        cmd = ["docker", "compose", "-f", str(compose_file)]

        # Add --env-file options for .env and config/*.env files
        env_file = self.project_root / ".env"
        if env_file.exists():
            cmd.extend(["--env-file", str(env_file)])

        # Add config/ads.env if exists
        ads_env_file = self.config_dir / "ads.env"
        if ads_env_file.exists():
            cmd.extend(["--env-file", str(ads_env_file)])

        if profile:
            cmd.extend(["--profile", profile])

        cmd.extend(["up", "-d"])
        if wait:
            cmd.append("--wait")

        # Merge external config with provided env
        run_env = os.environ.copy()
        # Load .env file for HOST_PROJECT_ROOT (needed for DooD compatibility)
        env_file = self.project_root / ".env"
        if env_file.exists():
            run_env.update(load_env_file(env_file))
        # Fallback to project_root if HOST_PROJECT_ROOT not in .env
        if "HOST_PROJECT_ROOT" not in run_env:
            run_env["HOST_PROJECT_ROOT"] = str(self.project_root)
        run_env.update(self.load_external_config())
        if env:
            run_env.update(env)

        try:
            subprocess.run(
                cmd,
                cwd=skill_dir,
                env=run_env,
                check=True,
            )
            print(f"  {skill_name} started successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  Failed to start {skill_name}: {e}")
            return False

    def stop_skill(
        self,
        skill_name: str,
        tier: str = "low_level",
        profile: Optional[str] = None,
    ) -> bool:
        """Stop a skill.

        Args:
            skill_name: Name of the skill
            tier: Skill tier
            profile: Docker Compose profile if used

        Returns:
            True if successful, False otherwise
        """
        compose_file = self.get_skill_compose_file(skill_name, tier)
        if not compose_file:
            print(f"Error: docker-compose.yml not found for skill '{skill_name}'")
            return False

        skill_dir = compose_file.parent
        print(f"Stopping skill: {skill_name}")

        cmd = ["docker", "compose", "-f", str(compose_file)]
        if profile:
            cmd.extend(["--profile", profile])
        cmd.append("down")

        # Set environment for docker compose
        run_env = os.environ.copy()
        # Load .env file for HOST_PROJECT_ROOT (needed for DooD compatibility)
        env_file = self.project_root / ".env"
        if env_file.exists():
            run_env.update(load_env_file(env_file))
        if "HOST_PROJECT_ROOT" not in run_env:
            run_env["HOST_PROJECT_ROOT"] = str(self.project_root)

        try:
            subprocess.run(cmd, cwd=skill_dir, env=run_env, check=True)
            print(f"  {skill_name} stopped successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  Failed to stop {skill_name}: {e}")
            return False

    def skill_logs(
        self,
        skill_name: str,
        tier: str = "low_level",
        follow: bool = False,
        tail: int = 100,
    ) -> None:
        """Show logs for a skill."""
        compose_file = self.get_skill_compose_file(skill_name, tier)
        if not compose_file:
            print(f"Error: docker-compose.yml not found for skill '{skill_name}'")
            return

        skill_dir = compose_file.parent
        cmd = ["docker", "compose", "-f", str(compose_file), "logs"]
        if follow:
            cmd.append("-f")
        cmd.extend(["--tail", str(tail)])

        # Set environment for docker compose
        run_env = os.environ.copy()
        # Load .env file for HOST_PROJECT_ROOT (needed for DooD compatibility)
        env_file = self.project_root / ".env"
        if env_file.exists():
            run_env.update(load_env_file(env_file))
        if "HOST_PROJECT_ROOT" not in run_env:
            run_env["HOST_PROJECT_ROOT"] = str(self.project_root)

        subprocess.run(cmd, cwd=skill_dir, env=run_env, check=False)

    def start_skill_profile(
        self,
        skills: List[Dict],
        env: Optional[Dict[str, str]] = None,
        env_files: Optional[List[str]] = None,
    ) -> bool:
        """Start multiple skills from a skill-based profile.

        Args:
            skills: List of skill configurations with keys:
                - name: Skill name (required)
                - tier: Skill tier (default: 'low_level')
                - compose_profile: Docker Compose profile (e.g., 'ads')
                - depends_on: List of skill names to wait for
                - wait_for_healthy: Wait for health check
                - environment: Additional environment variables
            env: Global environment variables
            env_files: List of env files to load (e.g., ['config/ads.env'])

        Returns:
            True if all skills started successfully
        """
        # Load environment from files
        run_env = {}
        if env_files:
            for env_file in env_files:
                env_path = self.project_root / env_file
                if env_path.exists():
                    run_env.update(load_env_file(env_path))
                else:
                    print(f"  Warning: env file not found: {env_file}")

        # Load external config (config/*.env)
        run_env.update(self.load_external_config())

        # Add global environment
        if env:
            run_env.update(env)

        print(f"Starting {len(skills)} skill(s)...")
        if env_files:
            print(f"  Env files: {env_files}")

        for skill_config in skills:
            skill_name = skill_config.get("name")
            tier = skill_config.get("tier", "low_level")
            compose_profile = skill_config.get("compose_profile")
            wait = skill_config.get("wait_for_healthy", True)
            skill_env = skill_config.get("environment", {})

            # Merge environments
            merged_env = {**run_env, **skill_env}

            print(f"\n  Starting skill: {skill_name}")
            if compose_profile:
                print(f"    Compose profile: {compose_profile}")

            success = self.start_skill(
                skill_name=skill_name,
                tier=tier,
                env=merged_env,
                profile=compose_profile,
                wait=wait,
            )

            if not success:
                print(f"  Failed to start skill: {skill_name}")
                return False

        print("\nAll skills started successfully.")
        return True

    def stop_skill_profile(
        self,
        skills: List[Dict],
    ) -> bool:
        """Stop multiple skills (in reverse order).

        Args:
            skills: List of skill configurations

        Returns:
            True if all skills stopped successfully
        """
        print(f"Stopping {len(skills)} skill(s)...")

        # Stop in reverse order
        for skill_config in reversed(skills):
            skill_name = skill_config.get("name")
            tier = skill_config.get("tier", "low_level")
            compose_profile = skill_config.get("compose_profile")

            print(f"  Stopping {skill_name}...", end=" ", flush=True)
            success = self.stop_skill(skill_name, tier, compose_profile)
            print("OK" if success else "FAILED")

        return True
