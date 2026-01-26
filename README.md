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
```

## Configuration

karakuri-ctl looks for configuration in `infrastructure/` directory:

- `infrastructure/profiles/` - Profile definitions
- `infrastructure/environments/` - Environment configurations
- `infrastructure/robots/` - Robot-specific configurations

## License

Apache-2.0
