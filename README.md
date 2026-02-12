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

## Startup Orchestration

- `depends_on` は厳密に解決されます（未定義依存・循環依存はエラー）。
- 依存関係のないスキルは同一バッチで並列起動されます。
- 他スキルから依存されるスキルは、`wait_for_healthy: false` が設定されていても
  起動完了待ち（`--wait`）が強制されます。

## License

Apache-2.0
