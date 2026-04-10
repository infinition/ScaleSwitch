# ScaleSwitch

A lightweight Windows system tray utility for per-monitor DPI scaling and display management.

Right-click the tray icon to adjust scaling, resolution, and refresh rate for each connected monitor — no restart required.

## Features

- **Per-monitor DPI scaling** with instant application (no logoff needed)
- **Preset values**: 100% through 500% in 25% increments
- **Resolution switching** per display with automatic best refresh rate selection
- **Refresh rate control** per display
- **Start with Windows** toggle built into the context menu
- **Live tray icon** showing the current primary monitor scale percentage
- **Minimal footprint**: ~10 MB on disk, ~15 MB RAM

## How It Works

ScaleSwitch uses the undocumented `DisplayConfigSetDeviceInfo` API (type `-4`) — the same mechanism Windows Settings uses internally to apply DPI changes. This allows instant scaling without requiring a session restart.

Monitor enumeration relies on `D3DKMTOpenAdapterFromGdiDisplayName` to obtain adapter LUIDs, which works reliably for all connected displays regardless of `QueryDisplayConfig` coverage.

## Installation

### Pre-built binary

Download `ScaleSwitch.exe` from the [Releases](https://github.com/infinition/ScaleSwitch/releases) page and run it. The icon appears in the system tray.

### From source

```
pip install pystray Pillow
python scale_switch.py
```

### Build from source

```
pip install pyinstaller pystray Pillow
pyinstaller scale_switch.spec
```

The executable is output to `dist/ScaleSwitch.exe`.

## Usage

| Action | Method |
|---|---|
| Open menu | Right-click the tray icon |
| Change scale | Select a preset or use Increase/Decrease |
| Change resolution | Open the Resolution submenu |
| Change refresh rate | Open the Refresh Rate submenu |
| Auto-start | Toggle "Start with Windows" |
| Exit | Click "Quit" |

## Requirements

- Windows 10 or later
- Python 3.8+ (only when running from source)

## License

[MIT](LICENSE)
