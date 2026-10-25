"""Explicit SDK construction: no dotenv, global settings or implicit retries."""
import httpx
from langchain_openai import ChatOpenAI


def create_sdk(*, model, api_key, base_url, http_client: httpx.AsyncClient,
               call_seconds=10.0, max_output_tokens=650):
    """Caller owns the HTTP client lifetime and authorization for its transport.

    A provided client can use a local MockTransport for protocol tests. This
    factory alone grants no authorization to send real corpus data to a provider.
    """
    if not model or not api_key or not base_url:
        raise ValueError('Explicit model, key and endpoint required')
    if not 0 < call_seconds <= 30 or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 2000:
        raise ValueError('Invalid SDK output or timeout limit')
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
                      temperature=0, timeout=call_seconds, max_retries=0,
                      max_tokens=max_output_tokens, http_async_client=http_client)
