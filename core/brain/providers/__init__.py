from core.brain.Providers.baseProvider import BaseLLMProvider
from core.brain.Providers.customProvider import CustomProvider
from core.brain.Providers.bastenProviderMeta import BastenProviderMeta

from core.brain.config import AGENT_PRIMARY_PROVIDER, AGENT_FALLBACK_PROVIDER
from core.logger.logger import logger


def get_provider(provider_name: str = None) -> BaseLLMProvider:
    provider_name = (provider_name or AGENT_PRIMARY_PROVIDER).lower().strip()
    logger.info(f"🔧 Initializing provider: {provider_name}")

    if provider_name == "custom":
        return CustomProvider()

    if provider_name in ("basten", "baseten", "meta", "bastenmeta", "bastenprovidermeta"):
        return BastenProviderMeta()

    raise ValueError(
        f"❌ Unknown provider: {provider_name}. Choose 'custom' or 'basten'."
    )


def get_fallback_provider() -> BaseLLMProvider:
    return get_provider(AGENT_FALLBACK_PROVIDER)


__all__ = [
    "BaseLLMProvider",
    "CustomProvider",
    "BastenProviderMeta",
    "get_provider",
    "get_fallback_provider",
]