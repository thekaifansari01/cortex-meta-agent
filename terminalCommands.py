import asyncio
import getpass
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv

from core.logger.logger import logger
from core.security import load_decrypted_token

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(PROJECT_ROOT, ".cortex.lock")
SESSION_DIR = os.path.join(PROJECT_ROOT, "Data", "SessionCookies")

CRED_PATHS = {
    "whatsapp": os.path.join(SESSION_DIR, "auth_info_baileys", "creds.json"),
    "telegram": os.path.join(SESSION_DIR, "cortex_telegram_session.session"),
    "mail": os.path.join(SESSION_DIR, "token.enc"),
    "calendar": os.path.join(SESSION_DIR, "calendar_token.enc"),
}

SERVICE_LABELS = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "mail": "Gmail",
    "calendar": "Google Calendar",
}


def _is_pid_active_cortex(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False

    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        name = (proc.name() or "").lower()
        cmdline = " ".join(proc.cmdline() or []).lower()
        return any(k in name or k in cmdline for k in ("python", "cortex"))
    except ImportError:
        pass
    except Exception:
        return False

    if platform.system() == "Windows":
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                creationflags=creationflags,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).lower()
            if str(pid) in output and ("python" in output or "cortex" in output):
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def is_cortex_running() -> bool:
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            raise ValueError("Empty lock file")
        pid = int(content.splitlines()[0].strip())
        if _is_pid_active_cortex(pid):
            return True
    except ValueError:
        logger.warning("Lock file corrupted. Proceeding with auto-cleanup.")
    except Exception:
        pass

    remove_lock_file()
    return False


def create_lock_file() -> None:
    try:
        temp_lock = f"{LOCK_FILE}.{os.getpid()}.tmp"
        with open(temp_lock, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_lock, LOCK_FILE)
    except Exception as e:
        logger.warning(f"Could not create lock file: {e}")


def remove_lock_file() -> None:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception as e:
        logger.warning(f"Could not remove lock file: {e}")


def _handle_remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


def safe_delete(path: str, retries: int = 3, delay: float = 0.5) -> bool:
    if not os.path.lexists(path):
        return False
    for attempt in range(1, retries + 1):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, onerror=_handle_remove_readonly)
                return not os.path.lexists(path)
            else:
                try:
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                except Exception:
                    pass
                os.remove(path)
                return not os.path.lexists(path)
        except PermissionError:
            if attempt < retries:
                time.sleep(delay)
            else:
                return False
        except FileNotFoundError:
            return True
        except Exception:
            if attempt < retries:
                time.sleep(delay)
            else:
                return False
    return False


def get_user_confirmation(prompt_text: str) -> bool:
    while True:
        try:
            ans = input(f"{prompt_text} (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Invalid input. Please enter 'y' or 'n'.")


def _prompt_secret(prompt_text: str) -> str:
    try:
        if sys.stdin and sys.stdin.isatty():
            value = getpass.getpass(prompt_text).strip()
            if value:
                return value
    except Exception:
        pass
    try:
        return input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def deleteMemory() -> None:
    try:
        target_folder = os.path.join(PROJECT_ROOT, "Data", "cortex_memory")
        if not os.path.exists(target_folder):
            logger.info("Memory is already cleared. Nothing to delete.")
            return

        if safe_delete(target_folder):
            logger.info("Memory cleared successfully.")
        else:
            logger.warning("Could not clear memory completely. Please check file permissions.")
    except Exception as e:
        logger.error(f"Error encountered while clearing memory: {e}")


def _revoke_google_token(token_file_path: str) -> None:
    if not os.path.exists(token_file_path):
        return
    try:
        token_data = load_decrypted_token(token_file_path)
        if not isinstance(token_data, dict):
            return
        revoke_target = token_data.get("refresh_token") or token_data.get("token") or token_data.get("access_token")
        if revoke_target:
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": revoke_target},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=5,
            )
    except Exception:
        pass


async def _revoke_telegram_session() -> None:
    session_base = os.path.join(SESSION_DIR, "cortex_telegram_session")
    session_file = f"{session_base}.session"
    if not os.path.exists(session_file):
        return
    try:
        from telethon import TelegramClient
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if not api_id.isdigit() or not api_hash:
            return
        logging.getLogger("telethon").setLevel(logging.CRITICAL)
        client = TelegramClient(session_base, int(api_id), api_hash)
        await asyncio.wait_for(client.connect(), timeout=5)
        try:
            if await asyncio.wait_for(client.is_user_authorized(), timeout=5):
                await asyncio.wait_for(client.log_out(), timeout=5)
        finally:
            await client.disconnect()
    except Exception:
        pass


def deleteSessionCookies(*targets: str) -> None:
    if not targets:
        logger.warning("No service specified.")
        return

    paths_map = {
        "whatsapp": [
            os.path.join(SESSION_DIR, "auth_info_baileys"),
            os.path.join(SESSION_DIR, "chats.db"),
            os.path.join(SESSION_DIR, "chats.db-wal"),
            os.path.join(SESSION_DIR, "chats.db-shm"),
        ],
        "calendar": [
            os.path.join(SESSION_DIR, "calendar_token.enc"),
        ],
        "mail": [
            os.path.join(SESSION_DIR, "token.enc"),
        ],
        "telegram": [
            os.path.join(SESSION_DIR, "cortex_telegram_session.session"),
            os.path.join(SESSION_DIR, "cortex_telegram_session.session-journal"),
            os.path.join(SESSION_DIR, "cortex_telegram_session.session-wal"),
            os.path.join(SESSION_DIR, "cortex_telegram_session.session-shm"),
        ],
    }

    services_logged_out = []
    already_logged_out = []
    failed_services = []

    for target in targets:
        key = str(target).strip().lower()
        if key not in paths_map:
            failed_services.append(str(target))
            continue

        path_list = paths_map[key]
        exists = any(os.path.lexists(p) for p in path_list)
        service_name = SERVICE_LABELS.get(key, key.capitalize())

        if not exists:
            already_logged_out.append(service_name)
            continue

        if key in ("mail", "calendar"):
            _revoke_google_token(path_list[0])
        elif key == "telegram":
            try:
                asyncio.run(_revoke_telegram_session())
            except Exception:
                pass

        all_deleted = True
        for item_path in path_list:
            if os.path.lexists(item_path):
                if not safe_delete(item_path):
                    all_deleted = False

        if all_deleted:
            services_logged_out.append(service_name)
        else:
            failed_services.append(service_name)

    if already_logged_out:
        logger.info(f"Already logged out: {', '.join(already_logged_out)}")
    if services_logged_out:
        logger.info(f"Successfully logged out: {', '.join(services_logged_out)}")
    if failed_services:
        logger.warning(f"Failed to logout completely (files in use or invalid service): {', '.join(failed_services)}")


async def _run_telegram_login() -> bool:
    from telethon import TelegramClient
    from telethon.errors import PhoneCodeInvalidError, PhoneNumberInvalidError, SessionPasswordNeededError
    from telethon.utils import get_display_name

    logging.getLogger("telethon").setLevel(logging.CRITICAL)

    os.makedirs(SESSION_DIR, exist_ok=True)
    session_file = os.path.join(SESSION_DIR, "cortex_telegram_session")

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    print("\n" + "=" * 65)
    print(" 📱 \033[1;36mCORTEX TELEGRAM SECURE AUTHENTICATION\033[0m")
    print("=" * 65)

    if not api_id_raw or not api_hash or not api_id_raw.isdigit():
        print(" ❌ \033[1;31mERROR: Valid TELEGRAM_API_ID or TELEGRAM_API_HASH missing in .env\033[0m")
        print("=" * 65 + "\n")
        return False

    client = TelegramClient(session_file, int(api_id_raw), api_hash)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            print(" 💡 \033[1;33mTIP: You MUST include your country code (e.g., +919876543210)\033[0m")
            print("-" * 65)
            phone = input(" 📞 Enter Phone Number : ").strip()

            if not phone:
                print("\n ❌ \033[1;31mERROR: Phone number cannot be empty.\033[0m")
                print("=" * 65 + "\n")
                return False

            if not phone.startswith("+"):
                print(" ⚠️ \033[1;33mWarning: Country code missing! Prepending '+' is required.\033[0m")

            try:
                await client.send_code_request(phone)
                print(" 📩 \033[1;32mOTP sent successfully! Please check your Telegram app.\033[0m")
                code = input(" 🔑 Enter OTP Code     : ").strip()

                try:
                    await client.sign_in(phone=phone, code=code)
                except SessionPasswordNeededError:
                    print("\n 🔐 \033[1;33mTwo-Step Verification (2FA) is enabled.\033[0m")
                    password = _prompt_secret(" 🔑 Enter 2FA Password : ")
                    await client.sign_in(password=password)
            except PhoneNumberInvalidError:
                print("\n ❌ \033[1;31mERROR: Invalid Phone Number! Did you forget the country code?\033[0m")
                print("=" * 65 + "\n")
                return False
            except PhoneCodeInvalidError:
                print("\n ❌ \033[1;31mERROR: Invalid OTP Code entered.\033[0m")
                print("=" * 65 + "\n")
                return False
            except Exception as e:
                print(f"\n ❌ \033[1;31mAUTHENTICATION FAILED: {e}\033[0m")
                print("=" * 65 + "\n")
                return False

        me = await client.get_me()
        name = get_display_name(me)
        print(f"\n ✅ \033[1;32mSUCCESS: Logged in successfully as {name}!\033[0m")
        print(" 📁 Session securely saved to: Data/SessionCookies/")
        print("=" * 65 + "\n")

        try:
            await client.send_message("me", "🤖 **Cortex Telegram Session Authenticated Successfully!**")
        except Exception:
            pass

        return True
    finally:
        await client.disconnect()


def login_service(service: str) -> None:
    svc_name = SERVICE_LABELS.get(service, service.capitalize())

    if service in CRED_PATHS and os.path.exists(CRED_PATHS[service]):
        if not get_user_confirmation(f"{svc_name} is already logged in. Do you want to overwrite the existing session?"):
            logger.info(f"Skipping {svc_name} login.")
            return
        deleteSessionCookies(service)

    if service == "whatsapp":
        baileys_dir = os.path.join(PROJECT_ROOT, "tools", "Messanger", "whatsapp", "BaileysServer")
        script_path = os.path.join(baileys_dir, "baileys_service.js")
        if not os.path.exists(script_path):
            logger.error("WhatsApp login service script not found.")
            return
        if not shutil.which("node"):
            logger.error("Node.js is not installed or not in the system PATH. Please install Node.js to use WhatsApp.")
            return

        logger.info("Starting WhatsApp login. Scan the QR code from the popup window.")
        logger.info("Press Ctrl+C after scanning once connection is confirmed.")
        try:
            subprocess.run(["node", script_path], cwd=baileys_dir, check=True)
            if os.path.exists(CRED_PATHS["whatsapp"]):
                logger.info("WhatsApp login process finished successfully.")
            else:
                logger.warning("WhatsApp login exited before credentials were saved.")
        except subprocess.CalledProcessError as e:
            logger.error(f"WhatsApp login process failed with exit code {e.returncode}.")
        except KeyboardInterrupt:
            if os.path.exists(CRED_PATHS["whatsapp"]):
                logger.info("WhatsApp credentials saved. Login completed successfully.")
            else:
                logger.info("WhatsApp login process cancelled by the user.")
        except Exception as e:
            logger.error(f"WhatsApp login encountered an unexpected error: {e}")

    elif service == "telegram":
        try:
            logger.info("Initializing Telegram Interactive Setup...")
            success = asyncio.run(_run_telegram_login())
            if success:
                logger.info("Telegram login process finished successfully.")
            else:
                logger.error("Telegram login could not be completed.")
        except ImportError as e:
            logger.error(f"Required module missing for Telegram login: {e}")
        except KeyboardInterrupt:
            logger.info("Telegram login process cancelled by the user.")
        except Exception as e:
            logger.error(f"Telegram login encountered an unexpected error: {e}")

    elif service == "mail":
        try:
            from tools.Messanger.email_manager import authenticate_gmail
            logger.info("Starting Gmail login. The browser will open for authentication.")
            result = authenticate_gmail(interactive=True)
            service_obj = result[0] if isinstance(result, tuple) else result
            if service_obj:
                logger.info("Gmail login successful.")
            else:
                logger.error("Gmail login failed or timed out.")
        except ImportError as e:
            logger.error(f"Required module missing for Gmail login: {e}")
        except KeyboardInterrupt:
            logger.info("Gmail login cancelled by the user.")
        except Exception as e:
            logger.error(f"Gmail login encountered an unexpected error: {e}")

    elif service == "calendar":
        try:
            from tools.Calendar.CalendarTool import authenticate_calendar
            logger.info("Starting Google Calendar login. The browser will open for authentication.")
            result = authenticate_calendar(interactive=True)
            service_obj = result[0] if isinstance(result, tuple) else result
            if service_obj:
                logger.info("Calendar login successful.")
            else:
                logger.error("Calendar login failed.")
        except ImportError as e:
            logger.error(f"Required module missing for Google Calendar login: {e}")
        except KeyboardInterrupt:
            logger.info("Calendar login cancelled by the user.")
        except Exception as e:
            logger.error(f"Calendar login encountered an unexpected error: {e}")
    else:
        logger.error(f"Unknown service specified: {service}")


def show_help_menu() -> None:
    help_text = """
====================================================================
                        CORTEX HELP MENU
====================================================================

USAGE:
    cortex                     Start Cortex AI Assistant
    cortex --help              Show this help menu

LOGIN COMMANDS (Cortex must be OFF):
    cortex login --whatsapp    Login to WhatsApp
    cortex login --telegram    Login to Telegram
    cortex login --mail        Login to Gmail
    cortex login --calendar    Login to Google Calendar
    cortex login --all         Login to all services

LOGOUT COMMANDS (Cortex must be OFF):
    cortex logout --whatsapp   Logout from WhatsApp
    cortex logout --telegram   Logout from Telegram
    cortex logout --mail       Logout from Gmail
    cortex logout --calendar   Logout from Google Calendar
    cortex logout --all        Logout from all services

MEMORY & RESET COMMANDS (Cortex must be OFF):
    cortex memory --clear      Clear Cortex memory and chat history
    cortex reset --hard        Factory reset (clear memory and log out all services)

====================================================================
"""
    logger.info(help_text)


def _extract_target_services(args_list: list[str]) -> tuple[list[str], list[str]]:
    flag_map = {
        "--whatsapp": "whatsapp",
        "--telegram": "telegram",
        "--mail": "mail",
        "--calendar": "calendar",
    }
    valid_services = []
    unknown_flags = []

    for item in args_list:
        if item in ("login", "logout"):
            continue
        if item == "--all":
            return ["whatsapp", "telegram", "mail", "calendar"], []
        if item in flag_map:
            svc = flag_map[item]
            if svc not in valid_services:
                valid_services.append(svc)
        else:
            unknown_flags.append(item)

    return valid_services, unknown_flags


def handle_cli_commands() -> bool:
    args = [arg.strip().lower() for arg in sys.argv[1:] if arg.strip()]
    if not args:
        return False

    dev_flags = {"test_jarvis", "no_wake"}
    non_dev_args = [a for a in args if a not in dev_flags and not a.startswith("voice=")]

    if not non_dev_args:
        return False

    if any(h in non_dev_args for h in ("help", "--help", "-h")):
        show_help_menu()
        return True

    subcommands = {"login", "logout", "memory", "reset"}
    matched_subcommands = [cmd for cmd in non_dev_args if cmd in subcommands]

    if not matched_subcommands:
        alias_map = {
            "--clear": "cortex memory --clear",
            "-c": "cortex memory --clear",
            "clear": "cortex memory --clear",
            "mem": "cortex memory --clear",
            "lgout": "cortex logout --all",
            "log": "cortex logout --all",
            "signout": "cortex logout --all",
            "purge": "cortex reset --hard",
            "factory": "cortex reset --hard",
            "--hard": "cortex reset --hard",
        }
        logger.error("Invalid command. Type 'cortex --help' to view the available commands.")
        for arg in non_dev_args:
            if arg in alias_map:
                logger.info(f"Did you mean: '{alias_map[arg]}'?")
                break
        return True

    unique_subcommands = list(dict.fromkeys(matched_subcommands))
    if len(unique_subcommands) > 1:
        logger.error(f"Conflicting subcommands specified: {', '.join(unique_subcommands)}. Please run one command at a time.")
        return True

    primary_cmd = unique_subcommands[0]

    if is_cortex_running():
        logger.error("Cortex is currently running! Please stop Cortex first before executing CLI setup commands.")
        sys.exit(1)

    logger.info("Executing command...")

    if primary_cmd == "login":
        services, unknown = _extract_target_services(non_dev_args)
        if unknown:
            logger.error(f"Unknown option(s) for login: {', '.join(unknown)}")
            logger.info("Valid options: --whatsapp, --telegram, --mail, --calendar, --all")
            return True
        if not services:
            logger.warning("Please specify a service: --whatsapp, --telegram, --mail, --calendar, or --all")
            logger.info("Type 'cortex --help' for usage details.")
        else:
            for svc in services:
                login_service(svc)

    elif primary_cmd == "logout":
        targets, unknown = _extract_target_services(non_dev_args)
        if unknown:
            logger.error(f"Unknown option(s) for logout: {', '.join(unknown)}")
            logger.info("Valid options: --whatsapp, --telegram, --mail, --calendar, --all")
            return True
        if not targets:
            logger.warning("Please specify a service: --whatsapp, --telegram, --mail, --calendar, or --all")
            logger.info("Type 'cortex --help' for usage details.")
        else:
            deleteSessionCookies(*targets)

    elif primary_cmd == "memory":
        extra_args = [a for a in non_dev_args if a != "memory"]
        if extra_args and all(a in ("--clear", "--purge") for a in extra_args):
            if get_user_confirmation("Are you sure you want to clear Cortex's memory? This will delete past context."):
                deleteMemory()
            else:
                logger.info("Memory clear operation aborted.")
        else:
            logger.warning("Use 'cortex memory --clear' to clear the memory.")
            logger.info("Type 'cortex --help' for usage details.")

    elif primary_cmd == "reset":
        extra_args = [a for a in non_dev_args if a != "reset"]
        if extra_args == ["--hard"]:
            if get_user_confirmation("WARNING: This will factory reset Cortex (clear all memory and log out all services). Are you absolutely sure?"):
                logger.info("Factory reset is in progress...")
                deleteMemory()
                deleteSessionCookies("whatsapp", "telegram", "mail", "calendar")
                logger.info("Factory reset completed successfully.")
            else:
                logger.info("Factory reset operation aborted.")
        else:
            logger.warning("Use 'cortex reset --hard' to perform a factory reset.")
            logger.info("Type 'cortex --help' for usage details.")

    return True