from core.logger.logger import logger
from core.utils.utils import resolve_pronouns
from core.brain.Processor.AgenticBrain import run_agentic_loop, make_result


def fetch_hybrid_response(raw_command: str, memory_instance=None):
    try:
        is_silent = False

        if memory_instance:
            if hasattr(memory_instance, "consume_silent_flag"):
                is_silent = memory_instance.consume_silent_flag()
            elif hasattr(memory_instance, "ephemeral"):
                is_silent = bool(memory_instance.ephemeral.pop("force_silent_agentic", False))

            if hasattr(memory_instance, "get_and_clear_feedback"):
                memory_instance.get_and_clear_feedback()

        return run_agentic_loop(raw_command, "", memory_instance, silent=is_silent)

    except Exception as e:
        logger.error(f"Agentic execution failed: {e}")
        return make_result(
            "Bhai, kuch technical issue aa gaya. Please try again.",
            priority="high"
        )


def process_command(raw_command: str, memory_instance=None):
    try:
        resolved_command = resolve_pronouns(raw_command)
        result = fetch_hybrid_response(resolved_command, memory_instance)

        if not result:
            return make_result("Connection failed. Please check your internet connection.", priority="low")

        return result

    except Exception as e:
        logger.error(f"process_command critical error: {e}")
        return make_result(
            "Bhai, command process karne me error aa gaya.",
            priority="high"
        )