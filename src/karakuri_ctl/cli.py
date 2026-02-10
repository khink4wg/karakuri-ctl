"""Main CLI for karakuri-ctl.

Skill-based architecture: all profiles use 1 skill = 1 docker-compose.yml.
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
    from .config_loader import ProfileLoader
except ImportError:
    from docker_manager import DockerManager, ServiceState
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


def cmd_up(args, docker: DockerManager, profiles: ProfileLoader) -> int:
    """Start a profile."""
    try:
        config = profiles.load_profile(args.profile)
    except FileNotFoundError:
        print(f"Error: Profile '{args.profile}' not found")
        return 1

    print(f"{Colors.CYAN}Starting profile: {args.profile}{Colors.RESET}")

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


def cmd_down(args, docker: DockerManager, profiles: ProfileLoader) -> int:
    """Stop services."""
    if args.profile:
        try:
            config = profiles.load_profile(args.profile)
        except FileNotFoundError:
            print(f"Error: Profile '{args.profile}' not found")
            return 1

        print(f"{Colors.CYAN}Stopping profile: {args.profile}{Colors.RESET}")
        skills = config.get("skills", [])
        env_files = config.get("env_files", [])
        docker.stop_skill_profile(skills, env_files=env_files)
        return 0
    else:
        print("Stopping all services...")
        docker.stop_all()

    return 0


def cmd_status(args, docker: DockerManager, profiles: ProfileLoader) -> int:
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


def cmd_profiles(args, docker: DockerManager, profiles: ProfileLoader) -> int:
    """List available profiles."""
    print(f"\n{Colors.BOLD}Available profiles:{Colors.RESET}\n")

    profile_names = profiles.list_profiles()
    if profile_names:
        for name in profile_names:
            try:
                info = profiles.get_profile_info(name)
                desc = info.get("description", "")
                skills = info.get("skills", [])
                print(f"  {Colors.BLUE}{name}{Colors.RESET}")
                if desc:
                    print(f"    {desc}")
                print(f"    Skills: {', '.join(skills)}")
                print()
            except Exception as e:
                print(f"  {Colors.RED}{name}{Colors.RESET} (error: {e})")
    else:
        print("  No profiles found.")

    return 0


def cmd_show(args, docker: DockerManager, profiles: ProfileLoader) -> int:
    """Show expanded profile configuration."""
    try:
        config = profiles.load_profile(args.profile)
        print(f"{Colors.CYAN}# Profile: {args.profile}{Colors.RESET}")
        print(f"{Colors.CYAN}# Fully expanded configuration:{Colors.RESET}\n")
    except FileNotFoundError:
        print(f"Error: Profile '{args.profile}' not found")
        return 1

    # Output as YAML or JSON
    if args.format == "json":
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        print(yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False))

    return 0


def cmd_logs(args, docker: DockerManager, profiles: ProfileLoader) -> int:
    """Show logs for a service."""
    docker.logs(args.service, follow=args.follow, tail=args.tail)
    return 0


def cmd_exec(args, docker: DockerManager, profiles: ProfileLoader) -> int:
    """Execute command in a running skill container with ROS/workspace bootstrap."""
    exec_command = list(args.exec_command or [])
    if exec_command and exec_command[0] == "--":
        exec_command = exec_command[1:]

    return docker.exec_skill(
        skill_name=args.skill,
        command=exec_command if exec_command else None,
        bootstrap_ros_ws=True,
    )


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

    # exec command
    exec_parser = subparsers.add_parser(
        "exec",
        help="Execute command in running skill container (auto source ROS/workspace)",
    )
    exec_parser.add_argument("skill", help="Skill/service name (e.g. ros2_control_skill)")
    exec_parser.add_argument(
        "exec_command",
        nargs=argparse.REMAINDER,
        help="Command tokens. Use '-- <cmd ...>'",
    )

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Find project root
    if args.directory:
        project_root = args.directory
    else:
        project_root = find_project_root()

    # Initialize profile loader (infrastructure profiles only)
    infrastructure_path = project_root / "infrastructure"
    if not infrastructure_path.is_dir():
        print(f"Error: infrastructure directory not found at {infrastructure_path}")
        return 1
    profile_loader = ProfileLoader(infrastructure_path)

    # Initialize Docker manager
    docker = DockerManager(project_root)

    # Handle profiles subcommand
    if args.command == "profiles":
        if hasattr(args, "profiles_cmd") and args.profiles_cmd == "show":
            return cmd_show(args, docker, profile_loader)
        else:
            return cmd_profiles(args, docker, profile_loader)

    # Dispatch command
    commands = {
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "profiles": cmd_profiles,
        "show": cmd_show,
        "logs": cmd_logs,
        "exec": cmd_exec,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args, docker, profile_loader)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
