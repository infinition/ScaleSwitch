"""
ScaleSwitch — Per-monitor DPI scaling from the system tray.
Lightweight, fast, single-file Windows utility.

Right-click tray icon → pick monitor → adjust scaling with +/- or preset buttons.
Includes startup-with-Windows toggle, per-monitor favorites with auto-restore,
screen rotation and clean exit.

Uses undocumented DisplayConfigSetDeviceInfo(-4) for instant DPI changes.
"""

import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import winreg
from pathlib import Path

import pystray
from pystray._util import win32 as _pystray_win32
from PIL import Image, ImageDraw, ImageFont

# ─── Constants ────────────────────────────────────────────────────────────────

APP_NAME = "ScaleSwitch"
APP_VERSION = "1.4"
SCALE_PRESETS = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]
DPI_VALS = SCALE_PRESETS  # All known DPI percentage values
SCALE_STEP = 25
SCALE_MIN = 100
SCALE_MAX = 500

STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Favorites are stored per monitor device path under %APPDATA%\ScaleSwitch
CONFIG_DIR = Path(os.environ.get("APPDATA", ".")) / APP_NAME
FAVORITES_FILE = CONFIG_DIR / "favorites.json"

# Windows API constants
QDC_ONLY_ACTIVE_PATHS = 0x00000002
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 0x00000002

# Undocumented DPI scaling device info types (reverse-engineered from Windows Settings)
DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE = -3
DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE = -4

# DEVMODE field flags
DM_BITSPERPEL = 0x00040000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
DM_DISPLAYORIENTATION = 0x00000080

# Display orientation values (dmDisplayOrientation)
DMDO_DEFAULT = 0
DMDO_90 = 1
DMDO_180 = 2
DMDO_270 = 3

# ChangeDisplaySettingsEx flags and return codes
CDS_UPDATEREGISTRY = 0x00000001
DISP_CHANGE_SUCCESSFUL = 0
ENUM_CURRENT_SETTINGS = -1

# ─── Favorites Storage ───────────────────────────────────────────────────────

def load_favorites() -> dict:
    """Load saved favorites. Returns {} when missing or unreadable."""
    if FAVORITES_FILE.is_file():
        try:
            return json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Failed to load favorites: {e}")
    return {}


def save_favorites(data: dict):
    """Persist favorites to disk, creating the config directory if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    FAVORITES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Win32 Structures ────────────────────────────────────────────────────────

class LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]

class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int32),  # signed — undocumented types are negative
        ("size", ctypes.c_uint32),
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
    ]

class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx_or_cloneGroupId", ctypes.c_uint32),
        ("statusFlags", ctypes.c_uint32),
    ]

class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]

class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx_or_desktopModeInfoIdx", ctypes.c_uint32),
        ("targetVideoSignalInfo_videoStandard", ctypes.c_uint32),
        ("targetVideoSignalInfo_vSyncFreq", DISPLAYCONFIG_RATIONAL),
        ("targetVideoSignalInfo_hSyncFreq", DISPLAYCONFIG_RATIONAL),
        ("targetVideoSignalInfo_activeSize_cx", ctypes.c_uint32),
        ("targetVideoSignalInfo_activeSize_cy", ctypes.c_uint32),
        ("targetVideoSignalInfo_totalSize_cx", ctypes.c_uint32),
        ("targetVideoSignalInfo_totalSize_cy", ctypes.c_uint32),
        ("targetVideoSignalInfo_scanLineOrdering", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("scaling", ctypes.c_uint32),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", ctypes.c_uint32),
        ("targetAvailable", ctypes.c_int32),
        ("statusFlags", ctypes.c_uint32),
    ]

class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", ctypes.c_uint32),
    ]

class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("infoType", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("adapterId", LUID),
        ("data", ctypes.c_byte * 64),
    ]

class DISPLAYCONFIG_TARGET_DEVICE_NAME_FLAGS(ctypes.Structure):
    _fields_ = [("value", ctypes.c_uint32)]

class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", DISPLAYCONFIG_TARGET_DEVICE_NAME_FLAGS),
        ("outputTechnology", ctypes.c_uint32),
        ("edidManufactureId", ctypes.c_uint16),
        ("edidProductCodeId", ctypes.c_uint16),
        ("connectorInstance", ctypes.c_uint32),
        ("monitorFriendlyDeviceName", ctypes.c_wchar * 64),
        ("monitorDevicePath", ctypes.c_wchar * 128),
    ]

class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("viewGdiDeviceName", ctypes.c_wchar * 32),
    ]

# Undocumented structs used by Windows Settings to get/set DPI immediately
class DISPLAYCONFIG_SOURCE_DPI_SCALE_GET(ctypes.Structure):
    """Used with DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE (-3).
    Returns min, max, and current DPI relative to recommended."""
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("minScaleRel", ctypes.c_int32),
        ("curScaleRel", ctypes.c_int32),
        ("maxScaleRel", ctypes.c_int32),
    ]

class DISPLAYCONFIG_SOURCE_DPI_SCALE_SET(ctypes.Structure):
    """Used with DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE (-4).
    Sets DPI relative to the recommended value."""
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("scaleRel", ctypes.c_int32),
    ]

class _DEVMODEW_UNION_DISPLAY(ctypes.Structure):
    """Display variant of the DEVMODEW union (16 bytes)."""
    _fields_ = [
        ("dmPosition_x", ctypes.c_int32),
        ("dmPosition_y", ctypes.c_int32),
        ("dmDisplayOrientation", ctypes.c_uint32),
        ("dmDisplayFixedOutput", ctypes.c_uint32),
    ]

class _DEVMODEW_UNION(ctypes.Union):
    """Union inside DEVMODEW — printer vs display fields (16 bytes)."""
    _fields_ = [
        ("display", _DEVMODEW_UNION_DISPLAY),
        ("_printer_bytes", ctypes.c_byte * 16),
    ]

class DEVMODEW(ctypes.Structure):
    """sizeof must be 220 — this is validated by EnumDisplaySettingsExW."""
    _pack_ = 1
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),   # 64
        ("dmSpecVersion", ctypes.c_uint16),       # 2
        ("dmDriverVersion", ctypes.c_uint16),     # 2
        ("dmSize", ctypes.c_uint16),              # 2
        ("dmDriverExtra", ctypes.c_uint16),       # 2
        ("dmFields", ctypes.c_uint32),            # 4
        ("u", _DEVMODEW_UNION),                   # 16 (union)
        ("dmColor", ctypes.c_int16),              # 2
        ("dmDuplex", ctypes.c_int16),             # 2
        ("dmYResolution", ctypes.c_int16),        # 2
        ("dmTTOption", ctypes.c_int16),           # 2
        ("dmCollate", ctypes.c_int16),            # 2
        ("dmFormName", ctypes.c_wchar * 32),      # 64
        ("dmLogPixels", ctypes.c_uint16),         # 2
        ("dmBitsPerPel", ctypes.c_uint32),        # 4
        ("dmPelsWidth", ctypes.c_uint32),         # 4
        ("dmPelsHeight", ctypes.c_uint32),        # 4
        ("dmDisplayFlags", ctypes.c_uint32),      # 4
        ("dmDisplayFrequency", ctypes.c_uint32),  # 4
        ("dmICMMethod", ctypes.c_uint32),         # 4
        ("dmICMIntent", ctypes.c_uint32),         # 4
        ("dmMediaType", ctypes.c_uint32),         # 4
        ("dmDitherType", ctypes.c_uint32),        # 4
        ("dmReserved1", ctypes.c_uint32),         # 4
        ("dmReserved2", ctypes.c_uint32),         # 4
        ("dmPanningWidth", ctypes.c_uint32),      # 4
        ("dmPanningHeight", ctypes.c_uint32),     # 4
    ]

# ─── Display Helpers ──────────────────────────────────────────────────────────

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class D3DKMT_OPENADAPTERFROMGDIDISPLAYNAME(ctypes.Structure):
    """Input/output struct for D3DKMTOpenAdapterFromGdiDisplayName.
    Takes a GDI name like '\\\\.\\DISPLAY2' and returns the adapter LUID
    and VidPnSourceId — works for ALL monitors regardless of QueryDisplayConfig."""
    _fields_ = [
        ("DeviceName", ctypes.c_wchar * 32),   # in
        ("hAdapter", ctypes.c_uint32),          # out
        ("AdapterLuid", LUID),                  # out
        ("VidPnSourceId", ctypes.c_uint32),     # out
    ]


def _make_luid(low: int, high: int) -> LUID:
    """Create a fresh LUID from plain Python ints."""
    luid = LUID()
    luid.LowPart = low
    luid.HighPart = high
    return luid


def _open_adapter_from_gdi(gdi_name: str):
    """Use D3DKMTOpenAdapterFromGdiDisplayName to get adapter LUID + VidPnSourceId.
    This works for ALL monitors, unlike QueryDisplayConfig which can miss some."""
    req = D3DKMT_OPENADAPTERFROMGDIDISPLAYNAME()
    req.DeviceName = gdi_name
    ret = gdi32.D3DKMTOpenAdapterFromGdiDisplayName(ctypes.byref(req))
    if ret != 0:
        return None
    # Close the adapter handle (we only need the LUID)
    class D3DKMT_CLOSEADAPTER(ctypes.Structure):
        _fields_ = [("hAdapter", ctypes.c_uint32)]
    close_req = D3DKMT_CLOSEADAPTER()
    close_req.hAdapter = req.hAdapter
    gdi32.D3DKMTCloseAdapter(ctypes.byref(close_req))
    return {
        "adapter_lo": req.AdapterLuid.LowPart,
        "adapter_hi": req.AdapterLuid.HighPart,
        "source_id": req.VidPnSourceId,
    }


def _get_dpi_scaling_info(adapter_id: LUID, source_id: int):
    """Get current DPI scaling info for a source using undocumented API (-3).
    Returns dict with keys: current, recommended, minimum, maximum (all %)."""
    packet = DISPLAYCONFIG_SOURCE_DPI_SCALE_GET()
    packet.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE
    packet.header.size = ctypes.sizeof(DISPLAYCONFIG_SOURCE_DPI_SCALE_GET)
    packet.header.adapterId = adapter_id
    packet.header.id = source_id

    ret = user32.DisplayConfigGetDeviceInfo(ctypes.byref(packet))
    if ret != 0:
        return None

    # Clamp curScaleRel to valid range
    cur = max(packet.minScaleRel, min(packet.maxScaleRel, packet.curScaleRel))
    min_abs = abs(packet.minScaleRel)

    if min_abs + packet.maxScaleRel + 1 > len(DPI_VALS):
        return None

    return {
        "current": DPI_VALS[min_abs + cur],
        "recommended": DPI_VALS[min_abs],
        "minimum": DPI_VALS[0],  # always 100
        "maximum": DPI_VALS[min_abs + packet.maxScaleRel],
    }


def set_dpi_scale(adapter_id: LUID, source_id: int, scale_percent: int):
    """Set DPI scaling immediately using undocumented API (-4).
    No logoff required — applies instantly like Windows Settings."""
    info = _get_dpi_scaling_info(adapter_id, source_id)
    if info is None:
        raise RuntimeError("Cannot read current DPI scaling info")

    if scale_percent == info["current"]:
        return True

    scale_percent = max(info["minimum"], min(info["maximum"], scale_percent))

    try:
        idx_target = DPI_VALS.index(scale_percent)
    except ValueError:
        raise RuntimeError(f"Unsupported DPI value: {scale_percent}%")
    try:
        idx_recommended = DPI_VALS.index(info["recommended"])
    except ValueError:
        raise RuntimeError(f"Cannot find recommended DPI ({info['recommended']}%) in table")

    scale_rel = idx_target - idx_recommended

    packet = DISPLAYCONFIG_SOURCE_DPI_SCALE_SET()
    packet.header.type = DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE
    packet.header.size = ctypes.sizeof(DISPLAYCONFIG_SOURCE_DPI_SCALE_SET)
    packet.header.adapterId = adapter_id
    packet.header.id = source_id
    packet.scaleRel = scale_rel

    ret = user32.DisplayConfigSetDeviceInfo(ctypes.byref(packet))
    if ret != 0:
        raise RuntimeError(f"DisplayConfigSetDeviceInfo failed (error {ret})")

    return True


def get_current_display_mode(gdi_name: str):
    """Get the current resolution and refresh rate for a monitor."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if user32.EnumDisplaySettingsExW(gdi_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
        return {
            "width": dm.dmPelsWidth,
            "height": dm.dmPelsHeight,
            "hz": dm.dmDisplayFrequency,
            "bpp": dm.dmBitsPerPel,
        }
    return None


def get_display_modes(gdi_name: str):
    """Enumerate all available display modes for a monitor.
    Returns unique resolutions (sorted desc) and refresh rates per resolution."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)

    # Collect all modes (32bpp only for modern displays)
    mode_set = set()
    i = 0
    while user32.EnumDisplaySettingsExW(gdi_name, i, ctypes.byref(dm), 0):
        if dm.dmBitsPerPel >= 32:
            mode_set.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
        i += 1

    # Build resolutions list (unique w×h, sorted by area descending)
    res_set = sorted({(w, h) for w, h, _ in mode_set}, key=lambda r: r[0] * r[1], reverse=True)

    # Build refresh rates per resolution (sorted descending)
    hz_map = {}
    for w, h, hz in mode_set:
        hz_map.setdefault((w, h), set()).add(hz)
    hz_map = {k: sorted(v, reverse=True) for k, v in hz_map.items()}

    return res_set, hz_map


def set_display_mode(gdi_name: str, width: int, height: int, refresh_rate: int = 0):
    """Change resolution and refresh rate for a specific monitor. Applied immediately."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmBitsPerPel = 32
    if refresh_rate > 0:
        dm.dmFields |= DM_DISPLAYFREQUENCY
        dm.dmDisplayFrequency = refresh_rate

    ret = user32.ChangeDisplaySettingsExW(gdi_name, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
    if ret != DISP_CHANGE_SUCCESSFUL:
        raise RuntimeError(f"ChangeDisplaySettingsExW failed (error {ret})")
    return True


def get_display_orientation(gdi_name: str) -> int:
    """Return the current dmDisplayOrientation for a monitor."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if user32.EnumDisplaySettingsExW(gdi_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
        return dm.u.display.dmDisplayOrientation
    return DMDO_DEFAULT


def set_display_orientation(gdi_name: str, orientation: int):
    """Rotate a monitor to the given DMDO_* orientation.

    Width and height are swapped when switching between landscape and
    portrait, otherwise Windows rejects the mode.
    """
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsExW(gdi_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
        raise RuntimeError("Cannot read current display settings")

    width, height = dm.dmPelsWidth, dm.dmPelsHeight
    was_portrait = dm.u.display.dmDisplayOrientation % 2
    will_be_portrait = orientation % 2
    if was_portrait != will_be_portrait:
        width, height = height, width

    dm.u.display.dmDisplayOrientation = orientation
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYORIENTATION

    ret = user32.ChangeDisplaySettingsExW(gdi_name, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
    if ret != DISP_CHANGE_SUCCESSFUL:
        raise RuntimeError(f"ChangeDisplaySettingsExW failed (error {ret})")
    return True


def toggle_display_orientation(gdi_name: str) -> int:
    """Flip a monitor between landscape and portrait. Returns the new orientation."""
    current = get_display_orientation(gdi_name)
    new_orientation = DMDO_DEFAULT if current != DMDO_DEFAULT else DMDO_90
    set_display_orientation(gdi_name, new_orientation)
    return new_orientation


def get_monitor_info_list():
    """Return list of all real monitors with DPI info.

    Uses EnumDisplayMonitors to find all physical monitors, then
    D3DKMTOpenAdapterFromGdiDisplayName to get the adapter LUID + VidPnSourceId
    needed by the undocumented DPI APIs. This works for ALL monitors regardless
    of whether QueryDisplayConfig returns them.
    """
    MONITORINFOEXA = type("MONITORINFOEXA", (ctypes.Structure,), {
        "_fields_": [
            ("cbSize", ctypes.c_uint32),
            ("rcMonitor", ctypes.wintypes.RECT),
            ("rcWork", ctypes.wintypes.RECT),
            ("dwFlags", ctypes.c_uint32),
            ("szDevice", ctypes.c_wchar * 32),
        ]
    })

    hmonitors = []
    def _cb(hmon, hdc, lprect, lparam):
        hmonitors.append(hmon)
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_void_p
    )
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)

    monitors = []
    for idx, hmon in enumerate(hmonitors):
        mi = MONITORINFOEXA()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXA)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))

        gdi_name = mi.szDevice
        is_primary = bool(mi.dwFlags & 1)
        w = mi.rcMonitor.right - mi.rcMonitor.left
        h = mi.rcMonitor.bottom - mi.rcMonitor.top

        # Get adapter LUID + source ID from kernel
        adapter_info = _open_adapter_from_gdi(gdi_name)
        if not adapter_info:
            continue

        adapter_id = _make_luid(adapter_info["adapter_lo"], adapter_info["adapter_hi"])
        source_id = adapter_info["source_id"]

        # Get DPI info
        dpi_info = _get_dpi_scaling_info(adapter_id, source_id)
        if not dpi_info:
            continue

        # Friendly name + stable device path via display config target
        friendly_name = gdi_name
        device_path = ""
        try:
            target_info = _get_target_device_info(adapter_id, source_id)
            if target_info:
                friendly_name = target_info["name"] or gdi_name
                device_path = target_info["device_path"] or ""
        except Exception:
            pass

        # Get display modes (resolution + refresh rates)
        current_mode = get_current_display_mode(gdi_name)
        resolutions, hz_map = get_display_modes(gdi_name)
        orientation = get_display_orientation(gdi_name)

        monitors.append({
            "index": idx + 1,
            "gdi_name": gdi_name,
            "friendly_name": friendly_name,
            "device_path": device_path,
            "adapter_lo": adapter_info["adapter_lo"],
            "adapter_hi": adapter_info["adapter_hi"],
            "source_id": source_id,
            "width": w,
            "height": h,
            "primary": is_primary,
            "scale": dpi_info["current"],
            "max_scale": dpi_info["maximum"],
            "recommended": dpi_info["recommended"],
            "current_res": (current_mode["width"], current_mode["height"]) if current_mode else (w, h),
            "current_hz": current_mode["hz"] if current_mode else 60,
            "resolutions": resolutions,
            "hz_map": hz_map,
            "orientation": orientation,
        })

    return monitors


def _get_target_device_info(adapter_id: LUID, source_id: int):
    """Find the display config target for a source and return its friendly
    name plus the stable monitor device path.

    The device path is the favorites key — it survives reboots and GDI
    renumbering, unlike \\\\.\\DISPLAYn."""
    num_paths = ctypes.c_uint32()
    num_modes = ctypes.c_uint32()
    ret = user32.GetDisplayConfigBufferSizes(
        QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), ctypes.byref(num_modes)
    )
    if ret != 0:
        return None

    paths = (DISPLAYCONFIG_PATH_INFO * num_paths.value)()
    modes = (DISPLAYCONFIG_MODE_INFO * num_modes.value)()
    ret = user32.QueryDisplayConfig(
        QDC_ONLY_ACTIVE_PATHS,
        ctypes.byref(num_paths), paths,
        ctypes.byref(num_modes), modes,
        None,
    )
    if ret != 0:
        return None

    for i in range(num_paths.value):
        p = paths[i]
        # Match by adapter LUID + source ID
        if (p.sourceInfo.adapterId.LowPart == adapter_id.LowPart and
                p.sourceInfo.adapterId.HighPart == adapter_id.HighPart and
                p.sourceInfo.id == source_id):
            tgt = DISPLAYCONFIG_TARGET_DEVICE_NAME()
            tgt.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
            tgt.header.size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
            tgt.header.adapterId = p.targetInfo.adapterId
            tgt.header.id = p.targetInfo.id
            if user32.DisplayConfigGetDeviceInfo(ctypes.byref(tgt)) == 0:
                return {
                    "name": tgt.monitorFriendlyDeviceName or None,
                    "device_path": tgt.monitorDevicePath or None,
                }
    return None


# ─── Startup Management ──────────────────────────────────────────────────────

def get_exe_path() -> str:
    """Get path to current executable."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def is_startup_enabled() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return val == f'"{get_exe_path()}"'
    except (FileNotFoundError, OSError):
        return False


def toggle_startup(enabled: bool):
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
            0, winreg.KEY_SET_VALUE
        )
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{get_exe_path()}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Startup toggle error: {e}")


# ─── Icon Generation ─────────────────────────────────────────────────────────

def create_tray_icon(scale_value: int = 0) -> Image.Image:
    """Generate a crisp 64x64 tray icon showing current primary scale %."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background: rounded rectangle with accent color
    # Dark blue-gray base
    bg_color = (30, 36, 50, 240)
    accent = (80, 180, 255, 255)

    # Draw rounded rect background
    r = 10
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=r, fill=bg_color, outline=accent, width=2)

    # Draw scale percentage text
    label = f"{scale_value}" if scale_value else "DPI"
    try:
        font = ImageFont.truetype("arial.ttf", 20 if scale_value else 14)
        font_sm = ImageFont.truetype("arial.ttf", 11)
    except (IOError, OSError):
        font = ImageFont.load_default()
        font_sm = font

    # Center the main number
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - 5
    draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)

    # Draw "%" below
    pct = "%"
    bbox2 = draw.textbbox((0, 0), pct, font=font_sm)
    tw2 = bbox2[2] - bbox2[0]
    draw.text(((size - tw2) // 2, y + th + 1), pct, fill=accent, font=font_sm)

    return img


# ─── Tray Application ────────────────────────────────────────────────────────

class _TrayIcon(pystray.Icon):
    """pystray.Icon that also opens the menu on left-click.
    Windows only raises the context menu for WM_RBUTTONUP by default."""

    def _on_notify(self, wparam, lparam):
        if lparam == _pystray_win32.WM_LBUTTONUP:
            lparam = _pystray_win32.WM_RBUTTONUP
        super()._on_notify(wparam, lparam)


class ScaleSwitchApp:
    def __init__(self):
        self.icon: pystray.Icon = None
        self._lock = threading.RLock()

    def _get_monitors(self):
        """Safely get monitors."""
        try:
            return get_monitor_info_list()
        except Exception:
            return []

    def _build_menu(self):
        """Build the context menu dynamically with current monitor states."""
        monitors = self._get_monitors()
        favorites = load_favorites()
        items = []

        # Header
        items.append(pystray.MenuItem(
            f"{APP_NAME} v{APP_VERSION}",
            None, enabled=False
        ))
        items.append(pystray.Menu.SEPARATOR)

        # Per-monitor sections
        for mon in monitors:
            cr = mon["current_res"]
            primary_tag = "  [Primary]" if mon["primary"] else ""
            header = (
                f'{mon["friendly_name"]} — '
                f'{cr[0]}×{cr[1]} @ {mon["current_hz"]} Hz{primary_tag}'
            )
            items.append(pystray.MenuItem(header, None, enabled=False))

            # Current scale display
            rec_label = (
                f' (recommended: {mon["recommended"]}%)'
                if mon["recommended"] != mon["scale"]
                else ""
            )
            items.append(pystray.MenuItem(
                f'  Scale: {mon["scale"]}%{rec_label}',
                None, enabled=False
            ))

            # Quick favorite actions for this monitor
            dev_path = mon["device_path"]
            fav = favorites.get(dev_path, None)
            has_secondary = fav and "secondary" in fav

            if fav:
                items.append(pystray.MenuItem(
                    '  ▶ Restore Favorite (Primary)',
                    self._make_restore_favorite_handler(mon)
                ))
            if has_secondary:
                items.append(pystray.MenuItem(
                    '  ▶ Secondary Mode',
                    self._make_apply_secondary_handler(mon)
                ))

            items.append(pystray.Menu.SEPARATOR)

            # Preset buttons as submenu
            a_lo, a_hi, s_id = mon["adapter_lo"], mon["adapter_hi"], mon["source_id"]
            scale_items = []
            for preset in SCALE_PRESETS:
                if preset > mon["max_scale"]:
                    break
                scale_items.append(pystray.MenuItem(
                    f'{preset}%',
                    self._make_scale_handler(a_lo, a_hi, s_id, preset),
                    checked=lambda item, p=preset, s=mon["scale"]: p == s,
                ))

            items.append(pystray.MenuItem(
                '  Presets', pystray.Menu(*scale_items)
            ))

            # +/- buttons
            items.append(pystray.MenuItem(
                f'  Increase (+{SCALE_STEP}%)',
                self._make_adjust_handler(a_lo, a_hi, s_id, mon["scale"], +SCALE_STEP, mon["max_scale"]),
                enabled=mon["scale"] < mon["max_scale"],
            ))
            items.append(pystray.MenuItem(
                f'  Decrease (-{SCALE_STEP}%)',
                self._make_adjust_handler(a_lo, a_hi, s_id, mon["scale"], -SCALE_STEP, mon["max_scale"]),
                enabled=mon["scale"] > SCALE_MIN,
            ))

            # Resolution submenu
            gdi = mon["gdi_name"]
            cur_res = mon["current_res"]
            cur_hz = mon["current_hz"]
            res_items = []
            for rw, rh in mon["resolutions"]:
                res_items.append(pystray.MenuItem(
                    f'{rw}×{rh}',
                    self._make_resolution_handler(gdi, rw, rh, mon["hz_map"]),
                    checked=lambda item, r=(rw, rh), c=cur_res: r == c,
                ))
            if res_items:
                items.append(pystray.MenuItem(
                    f'  Resolution: {cur_res[0]}×{cur_res[1]}',
                    pystray.Menu(*res_items)
                ))

            # Refresh rate submenu (for current resolution)
            hz_list = mon["hz_map"].get(cur_res, [])
            if hz_list:
                hz_items = []
                for hz in hz_list:
                    hz_items.append(pystray.MenuItem(
                        f'{hz} Hz',
                        self._make_refresh_handler(gdi, cur_res[0], cur_res[1], hz),
                        checked=lambda item, h=hz, c=cur_hz: h == c,
                    ))
                items.append(pystray.MenuItem(
                    f'  Refresh Rate: {cur_hz} Hz',
                    pystray.Menu(*hz_items)
                ))

            # Rotation toggle
            orientation = mon.get("orientation", DMDO_DEFAULT)
            is_portrait = orientation in (DMDO_90, DMDO_270)
            rotate_label = (
                '  ⟳ Rotate to Landscape' if is_portrait else '  ⟳ Rotate to Portrait'
            )
            items.append(pystray.MenuItem(
                rotate_label,
                self._make_rotate_handler(gdi)
            ))

            # Save / remove favorites for this monitor
            items.append(pystray.Menu.SEPARATOR)

            items.append(pystray.MenuItem(
                '  ☆ Save as Favorite (Primary)',
                self._make_save_favorite_handler(mon)
            ))

            items.append(pystray.MenuItem(
                '  ☆ Save as Secondary',
                self._make_save_secondary_handler(mon)
            ))

            if fav:
                items.append(pystray.MenuItem(
                    '  ✖ Remove Favorites',
                    self._make_remove_favorite_handler(dev_path)
                ))

            items.append(pystray.Menu.SEPARATOR)

        # Favorites submenu (every saved monitor, connected or not)
        favorites = load_favorites()
        if favorites:
            # One block per saved monitor
            fav_menu_items = []

            for dev_path, fav in sorted(favorites.items(), key=lambda x: x[1].get("friendly_name", "")):
                label = f'Primary: {fav.get("friendly_name", "Unknown")}  —  {fav.get("width", "?")}×{fav.get("height", "?")} @ {fav.get("hz", "?")} Hz  [{fav.get("scale", "?")}%]'
                auto = fav.get("auto_restore", True)
                sec = fav.get("secondary", None)

                fav_menu_items.append(pystray.MenuItem(
                    label,
                    None, enabled=False
                ))
                fav_menu_items.append(pystray.MenuItem(
                    'Auto-restore at startup',
                    self._make_toggle_auto_restore_handler(dev_path),
                    checked=lambda item, a=auto: a,
                ))
                fav_menu_items.append(pystray.MenuItem(
                    '↺ Restore Now (Primary)',
                    self._make_restore_favorite_by_key_handler(dev_path)
                ))

                if sec:
                    sec_label = f'  Secondary: {sec.get("width", "?")}×{sec.get("height", "?")} @ {sec.get("hz", "?")} Hz  [{sec.get("scale", "?")}%]'
                    fav_menu_items.append(pystray.MenuItem(
                        sec_label,
                        None, enabled=False
                    ))
                    fav_menu_items.append(pystray.MenuItem(
                        '↺ Secondary Mode',
                        self._make_restore_secondary_by_key_handler(dev_path)
                    ))

                fav_menu_items.append(pystray.MenuItem(
                    '✖ Remove',
                    self._make_remove_favorite_handler(dev_path)
                ))
                fav_menu_items.append(pystray.Menu.SEPARATOR)

            items.append(pystray.MenuItem(
                '★ Favorites',
                pystray.Menu(*fav_menu_items[:-1])
            ))

            # Restore everything in one click
            items.append(pystray.MenuItem(
                '↺ Restore All Favorites',
                self._make_restore_all_handler()
            ))

            items.append(pystray.Menu.SEPARATOR)

        # Startup toggle
        items.append(pystray.MenuItem(
            'Start with Windows',
            self._toggle_startup,
            checked=lambda item: is_startup_enabled(),
        ))

        items.append(pystray.Menu.SEPARATOR)

        # Quit
        items.append(pystray.MenuItem("Quit", self._quit))

        return pystray.Menu(*items)

    def _make_scale_handler(self, a_lo: int, a_hi: int, source_id: int, target_scale: int):
        def handler(icon, item):
            self._apply_scale(a_lo, a_hi, source_id, target_scale)
        return handler

    def _make_adjust_handler(self, a_lo: int, a_hi: int, source_id: int, current: int, delta: int, max_scale: int):
        def handler(icon, item):
            new_scale = max(SCALE_MIN, min(max_scale, current + delta))
            new_scale = round(new_scale / SCALE_STEP) * SCALE_STEP
            self._apply_scale(a_lo, a_hi, source_id, new_scale)
        return handler

    def _make_resolution_handler(self, gdi_name: str, w: int, h: int, hz_map: dict):
        def handler(icon, item):
            # Pick the highest refresh rate available for this resolution
            best_hz = max(hz_map.get((w, h), [0]))
            self._apply_display_mode(gdi_name, w, h, best_hz)
        return handler

    def _make_refresh_handler(self, gdi_name: str, w: int, h: int, hz: int):
        def handler(icon, item):
            self._apply_display_mode(gdi_name, w, h, hz)
        return handler

    def _make_rotate_handler(self, gdi_name: str):
        def handler(icon, item):
            self._rotate_screen(gdi_name)
        return handler

    # ─── Favorites ───────────────────────────────────────────────────────

    def _make_save_favorite_handler(self, mon: dict):
        """Save the monitor's current resolution/refresh/scale as its favorite."""
        dev_path = mon["device_path"]
        if not dev_path:
            # Fall back to the GDI name when no stable device path is available
            dev_path = f"__unknown__{mon['gdi_name']}"

        def handler(icon, item):
            with self._lock:
                try:

                    current_mode = get_current_display_mode(mon["gdi_name"])
                    dpi_info = _get_dpi_scaling_info(
                        _make_luid(mon["adapter_lo"], mon["adapter_hi"]),
                        mon["source_id"]
                    )

                    width = current_mode["width"] if current_mode else mon["current_res"][0]
                    height = current_mode["height"] if current_mode else mon["current_res"][1]
                    hz = current_mode["hz"] if current_mode else mon["current_hz"]
                    scale = dpi_info["current"] if dpi_info else mon["scale"]

                    favorites = load_favorites()
                    favorites[dev_path] = {
                        "friendly_name": mon["friendly_name"],
                        "width": width,
                        "height": height,
                        "hz": hz,
                        "scale": scale,
                        "auto_restore": True,
                    }
                    save_favorites(favorites)

                    self._refresh_icon()
                    if self.icon:
                        self.icon.notify(
                            f'Favorite saved: {mon["friendly_name"]} — '
                            f'{width}×{height} @ {hz} Hz, {scale}%',
                            APP_NAME
                        )
                except Exception as e:
                    if self.icon:
                        self.icon.notify(f"Error saving favorite: {e}", APP_NAME)

        return handler

    def _make_restore_favorite_handler(self, mon: dict):
        """Restore the saved primary favorite for a connected monitor."""
        dev_path = mon["device_path"]
        if not dev_path:
            dev_path = f"__unknown__{mon['gdi_name']}"

        def handler(icon, item):
            self._apply_favorite_for_monitor(mon)

        return handler

    def _make_restore_favorite_by_key_handler(self, dev_path: str):
        """Restore a favorite identified by its stored device path."""
        def handler(icon, item):
            with self._lock:
                try:
                    favorites = load_favorites()
                    fav = favorites.get(dev_path)
                    if not fav:
                        if self.icon:
                            self.icon.notify("Favorite not found", APP_NAME)
                        return

                    # The monitor must currently be connected
                    monitors = self._get_monitors()
                    mon = next((m for m in monitors if m["device_path"] == dev_path), None)

                    if not mon:
                        if self.icon:
                            self.icon.notify(
                                f'Monitor \'{fav.get("friendly_name", "?")}\' not found',
                                APP_NAME
                            )
                        return

                    self._apply_favorite_for_monitor(mon)
                except Exception as e:
                    if self.icon:
                        self.icon.notify(f"Error: {e}", APP_NAME)

        return handler

    def _make_restore_secondary_by_key_handler(self, dev_path: str):
        """Apply the stored secondary mode for a favorite by device path."""
        def handler(icon, item):
            with self._lock:
                try:
                    favorites = load_favorites()
                    fav = favorites.get(dev_path)
                    if not fav or "secondary" not in fav:
                        if self.icon:
                            self.icon.notify("No secondary mode saved", APP_NAME)
                        return

                    monitors = self._get_monitors()
                    mon = next((m for m in monitors if m["device_path"] == dev_path), None)

                    if not mon:
                        if self.icon:
                            self.icon.notify(
                                f'Monitor \'{fav.get("friendly_name", "?")}\' not found',
                                APP_NAME
                            )
                        return

                    self._apply_secondary_for_monitor(mon)
                except Exception as e:
                    if self.icon:
                        self.icon.notify(f"Error: {e}", APP_NAME)

        return handler

    def _apply_favorite_for_monitor(self, mon: dict):
        """Apply the stored primary favorite (scale + mode) to a monitor."""
        with self._lock:
            try:
                favorites = load_favorites()
                dev_path = mon.get("device_path", "")
                if not dev_path:
                    dev_path = f"__unknown__{mon['gdi_name']}"
                fav = favorites.get(dev_path)
                if not fav:
                    if self.icon:
                        self.icon.notify("No favorite saved for this monitor", APP_NAME)
                    return

                gdi = mon["gdi_name"]
                a_lo, a_hi, s_id = mon["adapter_lo"], mon["adapter_hi"], mon["source_id"]

                # Scale first — it is cheap and does not reset the mode
                adapter_id = _make_luid(a_lo, a_hi)
                set_dpi_scale(adapter_id, s_id, fav["scale"])

                # Then resolution + refresh rate
                set_display_mode(gdi, fav["width"], fav["height"], fav["hz"])

                self._refresh_icon()
                if self.icon:
                    self.icon.notify(
                        f'Favorite restored: {mon["friendly_name"]} — '
                        f'{fav["width"]}×{fav["height"]} @ {fav["hz"]} Hz, {fav["scale"]}%',
                        APP_NAME
                    )
            except Exception as e:
                if self.icon:
                    self.icon.notify(f"Error restoring favorite: {e}", APP_NAME)

    def _make_remove_favorite_handler(self, dev_path: str):
        """Delete every favorite entry stored for a device path."""
        def handler(icon, item):
            with self._lock:
                try:
                    favorites = load_favorites()
                    removed_name = favorites.get(dev_path, {}).get("friendly_name", "Unknown")
                    if dev_path in favorites:
                        del favorites[dev_path]
                        save_favorites(favorites)
                    self._refresh_icon()
                    if self.icon:
                        self.icon.notify(f"Favorite removed: {removed_name}", APP_NAME)
                except Exception as e:
                    if self.icon:
                        self.icon.notify(f"Error: {e}", APP_NAME)

        return handler

    # ─── Secondary ("alternate") mode ────────────────────────────────────

    def _make_save_secondary_handler(self, mon: dict):
        """Store the monitor's current state as its alternate ("secondary") mode."""
        dev_path = mon["device_path"]
        if not dev_path:
            dev_path = f"__unknown__{mon['gdi_name']}"

        def handler(icon, item):
            with self._lock:
                try:
                    current_mode = get_current_display_mode(mon["gdi_name"])
                    dpi_info = _get_dpi_scaling_info(
                        _make_luid(mon["adapter_lo"], mon["adapter_hi"]),
                        mon["source_id"]
                    )

                    width = current_mode["width"] if current_mode else mon["current_res"][0]
                    height = current_mode["height"] if current_mode else mon["current_res"][1]
                    hz = current_mode["hz"] if current_mode else mon["current_hz"]
                    scale = dpi_info["current"] if dpi_info else mon["scale"]

                    favorites = load_favorites()
                    if dev_path not in favorites:
                        # Seed the primary entry so the secondary has a parent
                        favorites[dev_path] = {
                            "friendly_name": mon["friendly_name"],
                            "width": width,
                            "height": height,
                            "hz": hz,
                            "scale": scale,
                            "auto_restore": True,
                        }
                    favorites[dev_path]["secondary"] = {
                        "width": width,
                        "height": height,
                        "hz": hz,
                        "scale": scale,
                    }
                    save_favorites(favorites)

                    self._refresh_icon()
                    if self.icon:
                        self.icon.notify(
                            f'Secondary saved: {mon["friendly_name"]} — '
                            f'{width}×{height} @ {hz} Hz, {scale}%',
                            APP_NAME
                        )
                except Exception as e:
                    if self.icon:
                        self.icon.notify(f"Error saving secondary: {e}", APP_NAME)

        return handler

    def _make_apply_secondary_handler(self, mon: dict):
        """Switch a connected monitor to its stored secondary mode."""
        def handler(icon, item):
            self._apply_secondary_for_monitor(mon)

        return handler

    def _apply_secondary_for_monitor(self, mon: dict):
        """Apply the stored secondary mode (scale + resolution) to a monitor."""
        with self._lock:
            try:
                favorites = load_favorites()
                dev_path = mon.get("device_path", "")
                if not dev_path:
                    dev_path = f"__unknown__{mon['gdi_name']}"
                fav = favorites.get(dev_path)
                if not fav or "secondary" not in fav:
                    if self.icon:
                        self.icon.notify("No secondary mode saved for this monitor", APP_NAME)
                    return

                sec = fav["secondary"]
                gdi = mon["gdi_name"]
                a_lo, a_hi, s_id = mon["adapter_lo"], mon["adapter_hi"], mon["source_id"]

                # Scale first, same ordering as the primary favorite
                adapter_id = _make_luid(a_lo, a_hi)
                set_dpi_scale(adapter_id, s_id, sec["scale"])

                # Then resolution + refresh rate
                set_display_mode(gdi, sec["width"], sec["height"], sec["hz"])

                self._refresh_icon()
                if self.icon:
                    self.icon.notify(
                        f'Secondary applied: {mon["friendly_name"]} — '
                        f'{sec["width"]}×{sec["height"]} @ {sec["hz"]} Hz, {sec["scale"]}%',
                        APP_NAME
                    )
            except Exception as e:
                if self.icon:
                    self.icon.notify(f"Error applying secondary: {e}", APP_NAME)

    def _make_toggle_auto_restore_handler(self, dev_path: str):
        """Toggle whether a favorite is reapplied when ScaleSwitch starts."""
        def handler(icon, item):
            with self._lock:
                try:
                    favorites = load_favorites()
                    if dev_path in favorites:
                        current = favorites[dev_path].get("auto_restore", True)
                        favorites[dev_path]["auto_restore"] = not current
                        save_favorites(favorites)
                    self._refresh_icon()
                except Exception as e:
                    if self.icon:
                        self.icon.notify(f"Error: {e}", APP_NAME)

        return handler

    def _make_restore_all_handler(self):
        """Menu handler for the "Restore All Favorites" entry."""
        def handler(icon, item):
            self._restore_all_favorites()

        return handler

    def _restore_all_favorites(self):
        """Reapply every auto-restore favorite for currently connected monitors."""
        with self._lock:
            try:
                favorites = load_favorites()
                if not favorites:
                    if self.icon:
                        self.icon.notify("No favorites saved", APP_NAME)
                    return

                monitors = self._get_monitors()
                restored = 0
                errors = []

                for dev_path, fav in favorites.items():
                    if not fav.get("auto_restore", True):
                        continue

                    mon = next((m for m in monitors if m["device_path"] == dev_path), None)
                    if not mon:
                        errors.append(f'{fav.get("friendly_name", "?")} (not connected)')
                        continue

                    try:
                        gdi = mon["gdi_name"]
                        adapter_id = _make_luid(mon["adapter_lo"], mon["adapter_hi"])
                        set_dpi_scale(adapter_id, mon["source_id"], fav["scale"])
                        set_display_mode(gdi, fav["width"], fav["height"], fav["hz"])
                        restored += 1
                    except Exception as e:
                        errors.append(f'{fav.get("friendly_name", "?")}: {e}')

                self._refresh_icon()
                if self.icon:
                    msg = f"Restored {restored} monitor(s)"
                    if errors:
                        msg += f' | Errors: {", ".join(errors[:3])}'
                    self.icon.notify(msg, APP_NAME)

            except Exception as e:
                if self.icon:
                    self.icon.notify(f"Error: {e}", APP_NAME)

    # ─── Apply helpers ───────────────────────────────────────────────────

    def _apply_display_mode(self, gdi_name: str, w: int, h: int, hz: int):
        with self._lock:
            try:
                set_display_mode(gdi_name, w, h, hz)
                self._refresh_icon()
                if self.icon:
                    self.icon.notify(
                        f"{w}×{h} @ {hz} Hz",
                        APP_NAME
                    )
            except Exception as e:
                if self.icon:
                    self.icon.notify(f"Error: {e}", APP_NAME)

    def _rotate_screen(self, gdi_name: str):
        with self._lock:
            try:
                new_orientation = toggle_display_orientation(gdi_name)
                self._refresh_icon()
                label = "Portrait" if new_orientation != DMDO_DEFAULT else "Landscape"
                if self.icon:
                    self.icon.notify(f"Screen rotated to {label}", APP_NAME)
            except Exception as e:
                if self.icon:
                    self.icon.notify(f"Error rotating screen: {e}", APP_NAME)

    def _apply_scale(self, a_lo: int, a_hi: int, source_id: int, scale: int):
        with self._lock:
            try:
                adapter_id = _make_luid(a_lo, a_hi)
                set_dpi_scale(adapter_id, source_id, scale)
                self._refresh_icon()
                if self.icon:
                    self.icon.notify(
                        f"Scale set to {scale}%",
                        APP_NAME
                    )
            except Exception as e:
                if self.icon:
                    self.icon.notify(f"Error: {e}", APP_NAME)

    def _refresh_icon(self):
        """Update icon image and menu after a change."""
        monitors = self._get_monitors()
        primary_scale = next(
            (m["scale"] for m in monitors if m["primary"]), 100
        )
        if self.icon:
            self.icon.icon = create_tray_icon(primary_scale)
            self.icon.menu = self._build_menu()
            self.icon.update_menu()

    def _toggle_startup(self, icon, item):
        toggle_startup(not is_startup_enabled())

    def _quit(self, icon, item):
        icon.stop()

    def run(self):
        monitors = self._get_monitors()
        primary_scale = next(
            (m["scale"] for m in monitors if m["primary"]), 100
        )

        self.icon = _TrayIcon(
            APP_NAME,
            icon=create_tray_icon(primary_scale),
            title=f"{APP_NAME} — Scale: {primary_scale}%",
            menu=self._build_menu(),
        )

        # Reapply saved favorites before the tray loop takes over
        self._auto_restore_on_startup()

        self.icon.run()

    def _auto_restore_on_startup(self):
        """Reapply auto-restore favorites once, at application startup."""
        try:
            favorites = load_favorites()
            if not favorites:
                return

            monitors = self._get_monitors()
            any_restored = False

            for dev_path, fav in favorites.items():
                if not fav.get("auto_restore", True):
                    continue
                mon = next((m for m in monitors if m["device_path"] == dev_path), None)
                if not mon:
                    continue

                try:
                    gdi = mon["gdi_name"]
                    adapter_id = _make_luid(mon["adapter_lo"], mon["adapter_hi"])

                    # Scale first, then resolution + refresh rate
                    set_dpi_scale(adapter_id, mon["source_id"], fav["scale"])

                    set_display_mode(gdi, fav["width"], fav["height"], fav["hz"])

                    any_restored = True
                    print(f'Auto-restored favorite for {mon["friendly_name"]}: '
                          f'{fav["width"]}×{fav["height"]} @ {fav["hz"]} Hz, {fav["scale"]}%')
                except Exception as e:
                    print(f'Auto-restore failed for {mon["friendly_name"]}: {e}')

            if any_restored:
                # The menu is rebuilt lazily on first open, so there is
                # nothing else to refresh here.
                return

        except Exception as e:
            print(f"Auto-restore startup error: {e}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    # Enable DPI awareness for accurate readings
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    app = ScaleSwitchApp()
    app.run()


if __name__ == "__main__":
    main()
