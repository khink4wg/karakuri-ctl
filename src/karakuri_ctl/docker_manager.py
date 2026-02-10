"""Docker Compose management for karakuri-ctl.

Skill-based architecture: 1 skill = 1 docker-compose.yml
"""

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import yaml


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
    """Manages Docker Compose operations for skill-based architecture."""

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

    def _resolve_env_files(self, env_files: Optional[List[str]]) -> List[Path]:
        """Resolve env file paths relative to project_root."""
        resolved: List[Path] = []
        if not env_files:
            return resolved
        for env_file in env_files:
            path = Path(env_file)
            if not path.is_absolute():
                path = self.project_root / path
            if path.exists():
                resolved.append(path)
            else:
                print(f"  Warning: env file not found: {env_file}")
        return resolved

    def _load_env_files(self, env_files: Optional[List[str]]) -> Dict[str, str]:
        """Load environment variables from a list of env files."""
        env: Dict[str, str] = {}
        for env_path in self._resolve_env_files(env_files):
            env.update(load_env_file(env_path))
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
        env_files: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run docker compose command."""
        cmd = self._build_compose_cmd(compose_files)
        for env_path in self._resolve_env_files(env_files):
            cmd.extend(["--env-file", str(env_path)])
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

    def stop_all(self, compose_files: Optional[List[str]] = None) -> bool:
        """Stop all running containers.

        If compose_files is specified, uses those files.
        Otherwise, discovers all running containers started by docker compose
        and stops them using their original compose files.
        """
        if compose_files:
            # Use specified compose files
            try:
                self._run_compose(["down"], compose_files=compose_files)
                return True
            except subprocess.CalledProcessError:
                return False

        # Discover all running containers and their compose files
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            container_ids = result.stdout.strip().split("\n")
            container_ids = [c for c in container_ids if c]

            if not container_ids:
                print("No running containers found.")
                return True

            # Group containers by their compose file
            # Only include containers whose compose file is under the project root
            project_root_str = str(self.project_root)
            compose_configs: dict[str, dict] = {}
            for container_id in container_ids:
                try:
                    result = subprocess.run(
                        ["docker", "inspect", container_id,
                         "--format", "{{index .Config.Labels \"com.docker.compose.project.config_files\"}}|{{index .Config.Labels \"com.docker.compose.project.environment_file\"}}|{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}|{{index .Config.Labels \"com.docker.compose.project\"}}"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    parts = result.stdout.strip().split("|")
                    if len(parts) >= 4 and parts[0]:
                        config_file = parts[0]
                        env_files = parts[1] if parts[1] else ""
                        working_dir = parts[2] if parts[2] else ""
                        project_name = parts[3] if parts[3] else ""

                        # Only include containers from this project
                        if not config_file.startswith(project_root_str):
                            continue

                        if config_file and config_file not in compose_configs:
                            compose_configs[config_file] = {
                                "env_files": env_files,
                                "working_dir": working_dir,
                                "project_name": project_name,
                            }
                except subprocess.CalledProcessError:
                    continue

            if not compose_configs:
                # No compose-managed containers, try default
                try:
                    self._run_compose(["down"])
                    return True
                except subprocess.CalledProcessError:
                    return False

            # Stop each compose project
            success = True
            for config_file, config in compose_configs.items():
                project_name = config.get("project_name", "")
                print(f"  Stopping project: {project_name} ({config_file})")

                cmd = ["docker", "compose", "-f", config_file]

                # Add environment files
                env_files = config.get("env_files", "")
                if env_files:
                    for env_file in env_files.split(","):
                        env_file = env_file.strip()
                        if env_file and Path(env_file).exists():
                            cmd.extend(["--env-file", env_file])

                cmd.append("down")

                try:
                    working_dir = config.get("working_dir") or str(self.project_root)
                    subprocess.run(cmd, cwd=working_dir, check=True)
                except subprocess.CalledProcessError:
                    print(f"    Failed to stop {project_name}")
                    success = False

            return success

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
        env_files: Optional[List[str]] = None,
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

        # Add --env-file options (default to .env when not provided)
        effective_env_files = env_files if env_files else [".env"]
        for env_path in self._resolve_env_files(effective_env_files):
            cmd.extend(["--env-file", str(env_path)])

        if profile:
            cmd.extend(["--profile", profile])

        cmd.extend(["up", "-d"])
        if wait:
            cmd.append("--wait")

        # Merge env files with provided env
        run_env = os.environ.copy()
        # Load env files for HOST_PROJECT_ROOT (needed for DooD compatibility)
        run_env.update(self._load_env_files(effective_env_files))
        # Fallback to project_root if HOST_PROJECT_ROOT not in .env
        if "HOST_PROJECT_ROOT" not in run_env:
            run_env["HOST_PROJECT_ROOT"] = str(self.project_root)
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
        env_files: Optional[List[str]] = None,
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
        effective_env_files = env_files if env_files else [".env"]
        for env_path in self._resolve_env_files(effective_env_files):
            cmd.extend(["--env-file", str(env_path)])
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
        effective_env_files = env_files if env_files else [".env"]
        run_env = self._load_env_files(effective_env_files)

        # Add global environment
        if env:
            run_env.update(env)

        print(f"Starting {len(skills)} skill(s)...")
        if effective_env_files:
            print(f"  Env files: {effective_env_files}")

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
                env_files=effective_env_files,
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
        env_files: Optional[List[str]] = None,
    ) -> bool:
        """Stop multiple skills (in reverse order).

        Args:
            skills: List of skill configurations

        Returns:
            True if all skills stopped successfully
        """
        print(f"Stopping {len(skills)} skill(s)...")

        effective_env_files = env_files if env_files else [".env"]
        # Stop in reverse order
        for skill_config in reversed(skills):
            skill_name = skill_config.get("name")
            tier = skill_config.get("tier", "low_level")
            compose_profile = skill_config.get("compose_profile")

            print(f"  Stopping {skill_name}...", end=" ", flush=True)
            success = self.stop_skill(
                skill_name,
                tier,
                env_files=effective_env_files,
                profile=compose_profile,
            )
            print("OK" if success else "FAILED")

        return True

    def exec_skill(
        self,
        skill_name: str,
        command: Optional[List[str]] = None,
        tier: str = "low_level",
        env_files: Optional[List[str]] = None,
        bootstrap_ros_ws: bool = True,
    ) -> int:
        """Execute a command inside a running skill container.

        Args:
            skill_name: Name of the skill (also used as compose service name)
            command: Command tokens to execute. If omitted, opens interactive shell.
            tier: Skill tier (reserved for future; compose is resolved by skill name)
            env_files: Env files for docker compose resolution
            bootstrap_ros_ws: Source ROS and workspace overlays before command

        Returns:
            Process return code from docker compose exec
        """
        compose_file = self.get_skill_compose_file(skill_name, tier)
        if not compose_file:
            print(f"Error: docker-compose.yml not found for skill '{skill_name}'")
            return 1

        skill_dir = compose_file.parent
        container_id = self._find_running_skill_container(skill_name, skill_dir)
        if not container_id:
            print(
                f"Error: running container not found for skill '{skill_name}'. "
                f"Start it first with 'karakuri-ctl up <profile>'."
            )
            return 1

        cmd = ["docker", "exec"]
        # Interactive shell only when requested and TTY is available.
        if command is None and sys.stdin.isatty() and sys.stdout.isatty():
            cmd.append("-it")
        cmd.extend([container_id, "bash", "-lc"])

        script_lines: List[str] = []
        if bootstrap_ros_ws:
            script_lines.extend([
                'source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash" >/dev/null 2>&1 || true',
                'if [ -f /workspace/install/setup.bash ]; then source /workspace/install/setup.bash >/dev/null 2>&1 || true; fi',
                'if [ -f /workspace/install/local_setup.bash ]; then source /workspace/install/local_setup.bash >/dev/null 2>&1 || true; fi',
                'if [ -f /ros2_ws/install/setup.bash ]; then source /ros2_ws/install/setup.bash >/dev/null 2>&1 || true; fi',
                'if [ -f /ros2_ws/install/local_setup.bash ]; then source /ros2_ws/install/local_setup.bash >/dev/null 2>&1 || true; fi',
            ])

        if command:
            script_lines.append(shlex.join(command))
        else:
            script_lines.append("exec bash -i")

        cmd.append("\n".join(script_lines))

        result = subprocess.run(
            cmd,
            check=False,
        )
        return result.returncode

    def _find_running_skill_container(self, skill_name: str, skill_dir: Path) -> Optional[str]:
        """Find running container ID for a skill service."""
        queries = [
            [
                "docker", "ps",
                "--filter", f"label=com.docker.compose.service={skill_name}",
                "--filter", f"label=com.docker.compose.project.working_dir={skill_dir}",
                "--format", "{{.ID}}",
            ],
            [
                "docker", "ps",
                "--filter", f"label=com.docker.compose.service={skill_name}",
                "--format", "{{.ID}}",
            ],
        ]

        for query in queries:
            result = subprocess.run(
                query,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                continue
            ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if ids:
                return ids[0]
        return None
