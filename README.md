# karakuri-ctl

Container orchestration CLI for karakuri robotics stack.

## Install

```bash
pip install karakuri-ctl
```

## Usage

```bash
# List available profiles
karakuri-ctl profiles

# Start a profile
karakuri-ctl up ads_ros2_control

# Check status
karakuri-ctl status

# Stop
karakuri-ctl down

# Execute command inside running skill container
# (automatically sources ROS + workspace overlays)
karakuri-ctl exec ros2_control_skill -- ros2 topic list

# Open interactive shell with bootstrap
karakuri-ctl exec ros2_control_skill
```

## Configuration

karakuri-ctl looks for configuration in `infrastructure/` directory:

- `infrastructure/profiles/` - Profile definitions
- `infrastructure/environments/` - Environment configurations
- `infrastructure/robots/` - Robot-specific configurations

## License

Apache-2.0
