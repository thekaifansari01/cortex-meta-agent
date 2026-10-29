import pyperclip
from core.logger.logger import logger

def read_clipboard():
    try:
        content = pyperclip.paste()
        if content:
            if len(content) > 100000:
                content = content[:100000] + "\n\n...[CLIPBOARD CONTENT TRUNCATED]..."
            logger.info("📋 Clipboard Tool: Read successful.")
            return content
        logger.info("📋 Clipboard Tool: Clipboard is empty.")
        return ""
    except Exception as e:
        logger.error(f"❌ Clipboard read failed: {e}")
        raise RuntimeError(f"Clipboard read failed: {e}")

def write_clipboard(text):
    """Writes text to the system clipboard."""
    try:
        pyperclip.copy(text)
        logger.info("📋 Clipboard Tool: Write successful.")
        return True
    except Exception as e:
        logger.error(f"❌ Clipboard write failed: {e}")
        return False