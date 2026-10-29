import os
import platform
import subprocess
import threading
import ctypes
from ctypes import cast, POINTER
import comtypes
from comtypes import CLSCTX_ALL
import pycaw.pycaw as pycaw_module
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import screen_brightness_control as sbc
from datetime import datetime
from PIL import ImageGrab
from core.logger.logger import logger

_com_lock = threading.Lock()
_com_initialized_threads = set()


def _ensure_com_initialized():
    tid = threading.get_ident()
    with _com_lock:
        if tid in _com_initialized_threads:
            return
    try:
        comtypes.CoInitialize()
        with _com_lock:
            _com_initialized_threads.add(tid)
    except Exception:
        pass


class SystemController:
    @staticmethod
    def _get_audio_interface():
        _ensure_com_initialized()
        devices = AudioUtilities.GetSpeakers()
        if hasattr(devices, '_dev'):
            devices = devices._dev
        elif not hasattr(devices, 'Activate') and hasattr(devices, 'id'):
            devices = pycaw_module.AudioUtilities.CreateDevice(devices.id)
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    @staticmethod
    def change_volume(amount: int, relative: bool = True):
        logger.info(f"🔊 Volume change: amount={amount}, relative={relative}")
        try:
            volume_interface = SystemController._get_audio_interface()
            current_vol = volume_interface.GetMasterVolumeLevelScalar()

            if relative:
                new_vol = current_vol + (amount / 100.0)
            else:
                new_vol = amount / 100.0

            new_vol = max(0.0, min(1.0, new_vol))
            volume_interface.SetMasterVolumeLevelScalar(new_vol, None)
            msg = f"Volume set to {int(new_vol * 100)}%"
            logger.info(f"✅ {msg}")
            return msg
        except Exception as pycaw_err:
            logger.warning(f"Pycaw volume error, trying fallback: {pycaw_err}")
            if platform.system() != "Windows":
                return f"Volume control error: {pycaw_err}"
            try:
                HWND_BROADCAST = 0xFFFF
                WM_APPCOMMAND = 0x0319
                APPCOMMAND_VOLUME_UP = 0x0a0000
                APPCOMMAND_VOLUME_DOWN = 0x090000

                if relative:
                    steps = abs(amount) // 2
                    cmd = APPCOMMAND_VOLUME_UP if amount > 0 else APPCOMMAND_VOLUME_DOWN
                    for _ in range(max(1, steps)):
                        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_APPCOMMAND, 0, cmd)
                    msg = f"Volume {'increased' if amount > 0 else 'decreased'} natively by ~{abs(amount)}%"
                else:
                    steps = int(amount) // 2
                    for _ in range(50):
                        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_APPCOMMAND, 0, APPCOMMAND_VOLUME_DOWN)
                    for _ in range(steps):
                        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_APPCOMMAND, 0, APPCOMMAND_VOLUME_UP)
                    msg = f"Volume reset and set natively to ~{amount}%"

                logger.info(f"✅ {msg}")
                return msg
            except Exception as native_err:
                msg = f"Volume control failed. Pycaw: {pycaw_err} | Native: {native_err}"
                logger.error(msg)
                return msg

    @staticmethod
    def toggle_speaker_mute():
        logger.info("🔇 Toggling speaker mute")
        try:
            volume_interface = SystemController._get_audio_interface()
            is_muted = volume_interface.GetMute()
            volume_interface.SetMute(not is_muted, None)
            state = "Muted" if not is_muted else "Unmuted"
            msg = f"System speakers are now {state}."
            logger.info(f"✅ {msg}")
            return msg
        except Exception as e:
            if platform.system() != "Windows":
                return f"Speaker mute error: {e}"
            try:
                HWND_BROADCAST = 0xFFFF
                WM_APPCOMMAND = 0x0319
                APPCOMMAND_VOLUME_MUTE = 0x80000
                ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_APPCOMMAND, 0, APPCOMMAND_VOLUME_MUTE)
                msg = "System speakers mute state toggled."
                logger.info(f"✅ {msg}")
                return msg
            except Exception as native_err:
                return f"Speaker mute error: {e} | {native_err}"

    @staticmethod
    def change_brightness(amount: int, relative: bool = True):
        logger.info(f"☀️ Brightness change: amount={amount}, relative={relative}")
        try:
            try:
                displays = sbc.list_monitors()
            except Exception:
                displays = []
            if not displays:
                try:
                    current = sbc.get_brightness()[0]
                    target = current + amount if relative else amount
                    target = max(0, min(100, target))
                    sbc.set_brightness(target)
                    return f"Brightness set to {target}%"
                except Exception as e:
                    return f"Brightness control error: {e}"
            for display in displays:
                try:
                    current_brightness = sbc.get_brightness(display=display)[0]
                    target = current_brightness + amount if relative else amount
                    target = max(0, min(100, target))
                    sbc.set_brightness(target, display=display)
                except Exception as disp_err:
                    logger.warning(f"Brightness error on {display}: {disp_err}")
            if relative:
                msg = f"Brightness adjusted by {amount:+d}% on {len(displays)} display(s)"
            else:
                msg = f"Brightness set to {max(0, min(100, amount))}% on {len(displays)} display(s)"
            logger.info(f"✅ {msg}")
            return msg
        except Exception as e:
            msg = f"Brightness control error: {str(e)}"
            logger.error(msg)
            return msg

    @staticmethod
    def lock_pc():
        logger.info("🔒 Locking PC")
        try:
            system = platform.system()
            if system == "Windows":
                ctypes.windll.user32.LockWorkStation()
                msg = "PC Locked successfully."
            elif system == "Darwin":
                subprocess.run(
                    ["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession", "-suspend"],
                    check=False, timeout=5
                )
                msg = "Screen locked (macOS)."
            else:
                locked = False
                for cmd in (
                    ["loginctl", "lock-session"],
                    ["gnome-screensaver-command", "-l"],
                    ["xdg-screensaver", "lock"],
                ):
                    try:
                        result = subprocess.run(cmd, capture_output=True, timeout=3)
                        if result.returncode == 0:
                            locked = True
                            break
                    except Exception:
                        continue
                msg = "PC Locked successfully." if locked else "Lock failed: no supported lock command available on this Linux environment."
            logger.info(f"✅ {msg}")
            return msg
        except Exception as e:
            msg = f"Lock error: {str(e)}"
            logger.error(msg)
            return msg

    @staticmethod
    def sleep_pc():
        logger.info("💤 Putting PC to sleep")
        try:
            system = platform.system()
            if system == "Windows":
                os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            elif system == "Darwin":
                os.system("pmset sleepnow")
            else:
                os.system("systemctl suspend")
            msg = "PC put to sleep mode."
            logger.info(f"✅ {msg}")
            return msg
        except Exception as e:
            msg = f"Sleep error: {str(e)}"
            logger.error(msg)
            return msg

    @staticmethod
    def capture_screenshot(filename: str = None, save_dir: str = None):
        logger.info(f"📸 Capturing screenshot, filename={filename}, save_dir={save_dir}")
        try:
            if not save_dir:
                save_dir = os.path.join(os.path.expanduser("~"), "Documents", "Cortex", "Screenshots")

            if filename:
                raw = str(filename).replace("\\", "/")
                clean = raw.split("/")[-1]
                clean = clean.replace("..", "_").strip()
                if not clean:
                    clean = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                if not clean.lower().endswith((".png", ".jpg", ".jpeg")):
                    clean += ".png"
                filepath = os.path.join(save_dir, clean)
            else:
                filepath = os.path.join(
                    save_dir,
                    f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
                )

            parent_dir = os.path.dirname(filepath)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            screenshot = ImageGrab.grab()
            screenshot.save(filepath)

            msg = f"Screenshot saved successfully. Absolute Path: {filepath}"
            logger.info(f"✅ {msg}")
            return msg
        except Exception as e:
            msg = f"Screenshot error: {str(e)}"
            logger.error(msg)
            return msg