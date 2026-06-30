from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI


PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "google": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash"],
}


def create_llm(provider: str, model: str, api_key: str) -> BaseChatModel:
    if provider == "openai":
        return ChatOpenAI(model=model, api_key=api_key)
    if provider == "anthropic":
        return ChatAnthropic(model=model, api_key=api_key)
    if provider == "google":
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key)
    raise ValueError(f"Unsupported provider: {provider}")
