"""Main CLI for karakuri-ctl.

Updated to support the new infrastructure-based configuration system.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

# Support both package and direct execution
try:
    from .docker_manager import DockerManager, ServiceState
    from .profile import ProfileManager, Profile, ServiceConfig
    from .config_loader import ProfileLoader
except ImportError:
    from docker_manager import DockerManager, ServiceState
    from profile import ProfileManager, Profile, ServiceConfig
    from config_loader import ProfileLoader


# ANSI colors
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def color_state(state: ServiceState) -> str:
    """Color-code service state."""
    if state == ServiceState.RUNNING:
        return f"{Colors.GREEN}{state.value}{Colors.RESET}"
    elif state == ServiceState.EXITED:
        return f"{Colors.GRAY}{state.value}{Colors.RESET}"
    elif state == ServiceState.NOT_FOUND:
        return f"{Colors.GRAY}not started{Colors.RESET}"
    else:
        return f"{Colors.YELLOW}{state.value}{Colors.RESET}"


def find_project_root() -> Path:
    """Find project root (directory containing docker-compose.yml or infrastructure/)."""
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        if (parent / "docker-compose.yml").exists():
            return parent
        if (parent / "docker-compose.yaml").exists():
            return parent
        if (parent / "infrastructure").is_dir():
            return parent
    return current


def is_skill_based_profile(config: dict) -> bool:
    """Check if profile uses skill-based architecture (1 skill = 1 docker-compose.yml)."""
    return config.get("profile_type") == "skill"


def convert_new_profile_to_legacy(config: dict) -> Profile:
    """Convert new profile format to legacy Profile object."""
    services = []
    for skill in config.get("skills", []):
        if isinstance(skill, dict):
            services.append(ServiceConfig(
                name=skill.get("name", ""),
                depends_on=skill.get("depends_on", []),
                wait_for_healthy=skill.get("wait_for_healthy", False),
                environment=skill.get("environment", {}),
            ))
        else:
            services.append(ServiceConfig(name=skill))

    # Get compose files from profile or use defaults
    compose_files = config.get("compose_files", [
        "docker-compose.yml",
        "infrastructure/docker/docker-compose.skills.yml",
    ])

    # Build environment from ROS settings and explicit environment section
    environment = {}
    ros_settings = config.get("ros", {})
    if ros_settings:
        environment["ROS_DOMAIN_ID"] = str(ros_settings.get("domain_id", 10))
        environment["RMW_IMPLEMENTATION"] = ros_settings.get("rmw_implementation", "rmw_fastrtps_cpp")

    # Merge explicit environment section (overrides ros settings)
    explicit_env = config.get("environment", {})
    if explicit_env:
        environment.update(explicit_env)

    return Profile(
        name=config.get("name", "unknown"),
        description=config.get("description", ""),
        compose_files=compose_files,
        environment=environment,
        services=services,
    )


def cmd_up(args, docker: DockerManager, legacy_profiles: ProfileManager,
           new_profiles: Optional[ProfileLoader]) -> int:
    """Start a profile."""
    config = None

    # Try new infrastructure profiles first
    if new_profiles:
        try:
            config = new_profiles.load_profile(args.profile)
        except FileNotFoundError:
            pass

    # Check if it's a skill-based profile
    if config and is_skill_based_profile(config):
        print(f"{Colors.CYAN}Using skill-based profile: {args.profile}{Colors.RESET}")

        # Extract skill configurations
        skills = config.get("skills", [])
        env_files = config.get("env_files", [])

        # Build global environment from ROS settings
        env = {}
        ros_settings = config.get("ros", {})
        if ros_settings:
            env["ROS_DOMAIN_ID"] = str(ros_settings.get("domain_id", 10))
            env["RMW_IMPLEMENTATION"] = ros_settings.get("rmw_implementation", "rmw_fastrtps_cpp")

        success = docker.start_skill_profile(skills, env=env, env_files=env_files)
        return 0 if success else 1

    # Legacy infrastructure profile
    if config:
        profile = convert_new_profile_to_legacy(config)
        print(f"{Colors.CYAN}Using infrastructure profile: {args.profile}{Colors.RESET}")
        success = docker.start_profile(profile, verbose=args.verbose)
        return 0 if success else 1

    # Fall back to legacy profiles
    try:
        profile = legacy_profiles.load_profile(args.profile)
        print(f"{Colors.GRAY}Using legacy profile: {args.profile}{Colors.RESET}")
        success = docker.start_profile(profile, verbose=args.verbose)
        return 0 if success else 1
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1


def cmd_down(args, docker: DockerManager, legacy_profiles: ProfileManager,
             new_profiles: Optional[ProfileLoader]) -> int:
    """Stop services."""
    if args.profile:
        config = None

        # Try new infrastructure profiles first
        if new_profiles:
            try:
                config = new_profiles.load_profile(args.profile)
            except FileNotFoundError:
                pass

        # Check if it's a skill-based profile
        if config and is_skill_based_profile(config):
            print(f"{Colors.CYAN}Stopping skill-based profile: {args.profile}{Colors.RESET}")
            skills = config.get("skills", [])
            docker.stop_skill_profile(skills)
            return 0

        # Legacy infrastructure profile
        if config:
            profile = convert_new_profile_to_legacy(config)
            docker.stop_profile(profile)
            return 0

        # Fall back to legacy profiles
        try:
            profile = legacy_profiles.load_profile(args.profile)
            docker.stop_profile(profile)
            return 0
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
    else:
        print("Stopping all services...")
        docker.stop_all()

    return 0


def cmd_status(args, docker: DockerManager, legacy_profiles: ProfileManager,
               new_profiles: Optional[ProfileLoader]) -> int:
    """Show status of all services."""
    statuses = docker.get_all_status()

    if not statuses:
        print("No containers running.")
        return 0

    print(f"\n{'SERVICE':<35} {'STATE':<15} {'CONTAINER ID':<15}")
    print("-" * 70)

    for status in statuses:
        container_id = status.container_id[:12] if status.container_id else "-"
        state_str = color_state(status.state)
        print(f"{status.name:<35} {state_str:<24} {container_id:<15}")

    print()
    return 0


def cmd_profiles(args, docker: DockerManager, legacy_profiles: ProfileManager,
                 new_profiles: Optional[ProfileLoader]) -> int:
    """List available profiles."""
    print(f"\n{Colors.BOLD}Available profiles:{Colors.RESET}\n")

    # List new infrastructure profiles
    if new_profiles:
        profile_names = new_profiles.list_profiles()
        if profile_names:
            print(f"  {Colors.CYAN}Infrastructure profiles:{Colors.RESET}")
            for name in profile_names:
                try:
                    info = new_profiles.get_profile_info(name)
                    desc = info.get("description", "")
                    skills = info.get("skills", [])
                    print(f"    {Colors.BLUE}{name}{Colors.RESET}")
                    if desc:
                        print(f"      {desc}")
                    print(f"      Skills: {', '.join(skills)}")
                    print()
                except Exception as e:
                    print(f"    {Colors.RED}{name}{Colors.RESET} (error: {e})")
            print()

    # List legacy profiles
    legacy_names = legacy_profiles.list_profiles()
    if legacy_names:
        print(f"  {Colors.GRAY}Legacy profiles:{Colors.RESET}")
        for name in legacy_names:
            try:
                info = legacy_profiles.get_profile_info(name)
                desc = info.get("description", "")
                services = info.get("services", [])
                print(f"    {Colors.GRAY}{name}{Colors.RESET}")
                if desc:
                    print(f"      {desc}")
                print(f"      Services: {', '.join(services)}")
                print()
            except Exception as e:
                print(f"    {Colors.RED}{name}{Colors.RESET} (error: {e})")

    return 0


def cmd_show(args, docker: DockerManager, legacy_profiles: ProfileManager,
             new_profiles: Optional[ProfileLoader]) -> int:
    """Show expanded profile configuration."""
    config = None

    # Try new infrastructure profiles first
    if new_profiles:
        try:
            config = new_profiles.load_profile(args.profile)
            print(f"{Colors.CYAN}# Infrastructure profile: {args.profile}{Colors.RESET}")
            print(f"{Colors.CYAN}# Fully expanded configuration:{Colors.RESET}\n")
        except FileNotFoundError:
            pass

    # Fall back to legacy profiles
    if config is None:
        try:
            profile = legacy_profiles.load_profile(args.profile)
            config = {
                "name": profile.name,
                "description": profile.description,
                "compose_files": profile.compose_files,
                "environment": profile.environment,
                "services": [
                    {
                        "name": s.name,
                        "depends_on": s.depends_on,
                        "wait_for_healthy": s.wait_for_healthy,
                        "environment": s.environment,
                    }
                    for s in profile.services
                ],
            }
            print(f"{Colors.GRAY}# Legacy profile: {args.profile}{Colors.RESET}\n")
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1

    # Output as YAML
    if args.format == "json":
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        print(yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False))

    return 0


def cmd_logs(args, docker: DockerManager, legacy_profiles: ProfileManager,
             new_profiles: Optional[ProfileLoader]) -> int:
    """Show logs for a service."""
    docker.logs(args.service, follow=args.follow, tail=args.tail)
    return 0


def main(argv: Optional[list] = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="karakuri-ctl",
        description="Container orchestration for karakuri robotics stack",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "-C", "--directory",
        type=Path,
        default=None,
        help="Project directory (default: auto-detect)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # up command
    up_parser = subparsers.add_parser("up", help="Start a profile")
    up_parser.add_argument("profile", help="Profile name to start")

    # down command
    down_parser = subparsers.add_parser("down", help="Stop services")
    down_parser.add_argument("profile", nargs="?", help="Profile to stop (default: all)")

    # status command
    subparsers.add_parser("status", help="Show status of all services")

    # profiles command
    profiles_parser = subparsers.add_parser("profiles", help="List available profiles")
    profiles_subparsers = profiles_parser.add_subparsers(dest="profiles_cmd")

    # profiles list (default)
    profiles_subparsers.add_parser("list", help="List profiles")

    # profiles show
    show_parser = profiles_subparsers.add_parser("show", help="Show expanded profile")
    show_parser.add_argument("profile", help="Profile name")
    show_parser.add_argument("--format", choices=["yaml", "json"], default="yaml",
                            help="Output format (default: yaml)")

    # show command (shorthand for profiles show)
    show_cmd_parser = subparsers.add_parser("show", help="Show expanded profile configuration")
    show_cmd_parser.add_argument("profile", help="Profile name")
    show_cmd_parser.add_argument("--format", choices=["yaml", "json"], default="yaml",
                                 help="Output format (default: yaml)")

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show logs for a service")
    logs_parser.add_argument("service", help="Service name")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    logs_parser.add_argument("-n", "--tail", type=int, default=100, help="Number of lines")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Find project root
    if args.directory:
        project_root = args.directory
    else:
        project_root = find_project_root()

    # Initialize profile managers
    # Legacy profiles (project_root/profiles/)
    legacy_profiles_dir = project_root / "profiles"
    legacy_profile_mgr = ProfileManager(legacy_profiles_dir)

    # New infrastructure profiles (project_root/infrastructure/)
    infrastructure_path = project_root / "infrastructure"
    new_profile_mgr = None
    if infrastructure_path.is_dir():
        new_profile_mgr = ProfileLoader(infrastructure_path)

    # Initialize Docker manager
    docker = DockerManager(project_root)

    # Handle profiles subcommand
    if args.command == "profiles":
        if hasattr(args, "profiles_cmd") and args.profiles_cmd == "show":
            return cmd_show(args, docker, legacy_profile_mgr, new_profile_mgr)
        else:
            return cmd_profiles(args, docker, legacy_profile_mgr, new_profile_mgr)

    # Dispatch command
    commands = {
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "profiles": cmd_profiles,
        "show": cmd_show,
        "logs": cmd_logs,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args, docker, legacy_profile_mgr, new_profile_mgr)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
