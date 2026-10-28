import os
import sys
import subprocess
import platform
import logging
import threading
from dotenv import load_dotenv
from core.utils.ProcessManager import proc_manager

load_dotenv()

_panel_process = None
_baileys_process = None


def start_agent_panel():
    global _panel_process
    stop_agent_panel()
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        panel_module_file = os.path.join(base_dir, "core", "ui", "AgentPanel", "AgentPanel.py")
        if not os.path.exists(panel_module_file):
            logging.warning(f"AgentPanel.py not found at {panel_module_file}, skipping start.")
            return
        creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        _panel_process = proc_manager.spawn(
            [sys.executable, "-m", "core.ui.AgentPanel.AgentPanel"],
            cwd=base_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags
        )
        logging.info("Agent panel started successfully (module mode).")
    except Exception as e:
        logging.error(f"Agent panel start failed: {e}")


def stop_agent_panel():
    global _panel_process
    if _panel_process:
        try:
            proc_manager.kill_process_tree(_panel_process.pid)
            _panel_process.wait(timeout=2)
            logging.info("Agent panel stopped successfully.")
        except Exception as e:
            logging.error(f"Error stopping Agent panel: {e}")
        _panel_process = None


def start_baileys_server():
    global _baileys_process
    stop_baileys_server()
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        baileys_dir = os.path.join(base_dir, "tools", "Messanger", "whatsapp", "BaileysServer")
        script_path = os.path.join(baileys_dir, "baileys_service.js")
        session_creds_path = os.path.join(base_dir, "Data", "SessionCookies", "auth_info_baileys", "creds.json")
        if os.path.exists(script_path):
            if not os.path.exists(session_creds_path):
                logging.info("WhatsApp not logged in. Skipping server start.")
                return
            creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
            _baileys_process = proc_manager.spawn(
                ["node", script_path],
                cwd=baileys_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags
            )
            logging.info("Baileys server started successfully.")
    except Exception as e:
        logging.error(f"Baileys server start failed: {e}")


def stop_baileys_server():
    global _baileys_process
    if _baileys_process:
        try:
            proc_manager.kill_process_tree(_baileys_process.pid)
            _baileys_process.wait(timeout=2)
            logging.info("Baileys server stopped successfully.")
        except Exception as e:
            logging.error(f"Error stopping Baileys server: {e}")
        _baileys_process = None


def is_baileys_running() -> bool:
    global _baileys_process
    if _baileys_process is None:
        return False
    return _baileys_process.poll() is None


def stop_all_services():
    logging.info("Initiating shutdown of all background services.")
    stop_tasks = [
        stop_agent_panel,
        stop_baileys_server,
    ]
    threads = []
    for task in stop_tasks:
        t = threading.Thread(target=task, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=2.5)