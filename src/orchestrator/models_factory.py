"""LLM Provider Factory for ShopHub Orchestrator.

Allows swapping model providers via configuration/environment variables while
defaulting to the offline ScriptedFakeChatModel (zero API keys required).
"""

import os
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

from src.orchestrator.fake_llm import ScriptedFakeChatModel


def get_chat_model(provider: Optional[str] = None) -> BaseChatModel:
    """Factory function returning a LangChain-compatible chat model.

    Configuration via:
    - provider argument
    - LLM_PROVIDER environment variable ("fake" | "openai" | "anthropic")

    Default is "fake" (no API key needed, operates completely offline).
    """
    selected_provider = (provider or os.getenv("LLM_PROVIDER", "fake")).strip().lower()

    if selected_provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required when LLM_PROVIDER='openai'")
        try:
            from langchain_openai import ChatOpenAI
            model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
            return ChatOpenAI(model=model_name, api_key=api_key, temperature=0.0)
        except ImportError:
            raise ImportError("langchain-openai package is required for OpenAI provider. Run: pip install langchain-openai")

    elif selected_provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required when LLM_PROVIDER='anthropic'")
        try:
            from langchain_anthropic import ChatAnthropic
            model_name = os.getenv("ANTHROPIC_MODEL_NAME", "claude-3-5-sonnet-20241022")
            return ChatAnthropic(model=model_name, api_key=api_key, temperature=0.0)
        except ImportError:
            raise ImportError("langchain-anthropic package is required for Anthropic provider. Run: pip install langchain-anthropic")

    # Default: Offline Fake LLM (R0/R9 requirements: zero API key needed)
    return ScriptedFakeChatModel()

