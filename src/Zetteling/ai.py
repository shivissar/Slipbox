"""
ai.py — AI provider abstraction for chat and embeddings.

Three backends: Anthropic, OpenAI-compatible (covers OpenAI itself plus
OpenRouter, Together, Groq, DeepSeek, and any other service implementing
the OpenAI API shape), and Ollama (local models running on the user's
own machine).

Each backend implements `chat(messages, max_tokens)` for note interrogation
and tag suggestions. Embeddings (for semantic search) come in M4.2.

The user picks a provider in /settings. Configuration is read fresh from
settings on every call so changes take effect without a restart. The
optional-import pattern (try/except ImportError) keeps the base app
running even if the provider's SDK is missing — the relevant feature
just disables itself with a clear message.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import settings

try:
    import anthropic as _anthropic_sdk
    ANTHROPIC_AVAILABLE = True
except ImportError:
    _anthropic_sdk = None
    ANTHROPIC_AVAILABLE = False

try:
    import openai as _openai_sdk
    OPENAI_AVAILABLE = True
except ImportError:
    _openai_sdk = None
    OPENAI_AVAILABLE = False

try:
    from google import genai as _genai_sdk
    GEMINI_AVAILABLE = True
except ImportError:
    _genai_sdk = None
    GEMINI_AVAILABLE = False

# Ollama uses plain HTTP via urllib, no SDK required.
OLLAMA_AVAILABLE = True


PROVIDERS = ["anthropic", "openai", "gemini", "ollama"]

PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI-compatible (OpenAI, OpenRouter, Together, Groq, DeepSeek, …)",
    "gemini": "Google Gemini",
    "ollama": "Ollama (local models)",
    "voyage": "Voyage AI (embeddings only)",
}

# v5.0: defaults refreshed in November 2026. The Gemini chat default is
# Google's "latest" alias, which Google moves to each new Flash release
# with two weeks' notice, so it does not go stale the way a pinned name
# does. gemini-2.0-flash (shut down 1 June 2026) and text-embedding-004
# (shut down 14 January 2026) were the previous defaults.
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-5.4-mini",
    "gemini": "gemini-flash-latest",
    "ollama": "qwen2.5:14b",
}

DEFAULT_EMBED_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-2",
    "ollama": "nomic-embed-text",
    "voyage": "voyage-3.5",
}

# Models their providers have shut down, with the date and a suggested
# replacement. Settings shows a warning if a saved model is on this list.
# Source: Google's Gemini API deprecations page, checked November 2026.
# Add entries here as providers announce shutdowns.
RETIRED_MODELS = {
    "gemini": {
        "text-embedding-004": ("14 January 2026", "gemini-embedding-2"),
        "embedding-001": ("30 October 2025", "gemini-embedding-2"),
        "gemini-embedding-exp": ("30 October 2025", "gemini-embedding-2"),
        "gemini-embedding-exp-03-07": ("30 October 2025", "gemini-embedding-2"),
        "gemini-2.0-flash": ("1 June 2026", "gemini-flash-latest"),
        "gemini-2.0-flash-001": ("1 June 2026", "gemini-flash-latest"),
        "gemini-2.0-flash-lite": ("1 June 2026", "gemini-flash-latest"),
        "gemini-2.0-flash-lite-001": ("1 June 2026", "gemini-flash-latest"),
    },
}


def _raise_if_retired(provider, model):
    """Fail fast, with a useful message, if the model has been shut down."""
    hit = RETIRED_MODELS.get(provider, {}).get(model)
    if hit:
        raise AIError(
            f"The model '{model}' was shut down by its provider on {hit[0]}. "
            f"Open Settings → AI and switch to '{hit[1]}'."
        )


def retired_model_warnings():
    """Return a list of warnings for saved chat or embedding models that
    their provider has shut down. Each warning is a dict with keys:
    role ("chat" or "embed"), provider, model, date, replacement."""
    out = []
    for role, prefix, defaults in (
        ("chat", "ai_chat_", DEFAULT_MODELS),
        ("embed", "ai_embed_", DEFAULT_EMBED_MODELS),
    ):
        provider = (settings.get(prefix + "provider", "") or "").strip()
        if not provider:
            continue
        model = (settings.get(prefix + "model", "") or "").strip() or defaults.get(provider, "")
        hit = RETIRED_MODELS.get(provider, {}).get(model)
        if hit:
            out.append({
                "role": role, "provider": provider, "model": model,
                "date": hit[0], "replacement": hit[1],
            })
    return out

# Anthropic does not offer its own embedding models; they recommend Voyage AI
# as their partner. Voyage is embeddings-only (no chat), so it's in
# EMBED_PROVIDERS but not PROVIDERS.
EMBED_PROVIDERS = ["openai", "gemini", "ollama", "voyage"]

DEFAULT_BASE_URLS = {
    "anthropic": "",  # SDK has its own
    "openai": "https://api.openai.com/v1",
    "gemini": "",  # SDK has its own
    "ollama": "http://127.0.0.1:11434",
    "voyage": "https://api.voyageai.com/v1",
}


def _normalize_base_url(url, default=""):
    """Clean up a base URL from settings. Handles three common user mistakes:
    1. Trailing/leading whitespace (from copy-paste)
    2. Trailing slashes (inconsistent with our /api/... path construction)
    3. Missing scheme (urllib gives a cryptic 'unknown url type' error
       when the scheme is missing, so we auto-prepend http:// for
       plain host:port strings that obviously mean HTTP)
    If the result is empty after stripping, returns the default."""
    s = (url or "").strip()
    if not s:
        return default
    # Strip trailing slashes
    s = s.rstrip("/")
    # If no scheme, auto-prepend http:// (not https:// because the most
    # common missing-scheme case is localhost:11434 for Ollama).
    if "://" not in s:
        s = "http://" + s
    return s


def _normalize_model_name(name, default=""):
    """Clean up a model name from settings. Handles three common user mistakes:
    1. Trailing/leading whitespace (from copy-paste, especially newlines)
    2. The 'models/' prefix on Gemini model names (the SDK adds this
       itself; if the user types it in explicitly, we get a double prefix
       which Gemini rejects as 'unexpected model name format')
    3. Empty string (falls back to the default)"""
    s = (name or "").strip()
    if not s:
        return default
    if s.startswith("models/"):
        s = s[len("models/"):]
    return s


class AIError(Exception):
    """Raised when an AI call fails. Message is user-facing."""


class AIProvider:
    """Abstract interface. Subclasses implement chat()."""
    name = "abstract"

    def chat(self, messages, max_tokens=1024):
        raise NotImplementedError

    def embed(self, text):
        """Return a list of floats representing the embedding of `text`.
        Subclasses that don't support embeddings raise AIError."""
        raise NotImplementedError

    def test_connection(self):
        """Minimal call to verify config. Returns (ok: bool, message: str)."""
        try:
            text = self.chat(
                [{"role": "user", "content": "Reply with exactly the word 'ok' and nothing else."}],
                max_tokens=20,
            )
            preview = text.strip()[:80] or "(empty response)"
            return True, f"Connection OK. Reply: {preview}"
        except AIError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Unexpected error: {e}"


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self):
        self.api_key = (settings.get("ai_chat_api_key", "") or "").strip()
        self.model = settings.get("ai_chat_model", "") or DEFAULT_MODELS["anthropic"]

    def embed(self, text):
        raise AIError(
            "Anthropic does not provide a native embeddings API. "
            "Configure a separate embedding provider (Ollama or "
            "OpenAI-compatible) in /settings."
        )

    def chat(self, messages, max_tokens=1024):
        if not ANTHROPIC_AVAILABLE:
            raise AIError("anthropic SDK not installed. Run: pip install anthropic")
        if not self.api_key:
            raise AIError("Anthropic API key not configured. Set it in /settings.")
        client = _anthropic_sdk.Anthropic(api_key=self.api_key)
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
            )
        except _anthropic_sdk.APIError as e:
            raise AIError(f"Anthropic API error: {e}")
        except Exception as e:
            raise AIError(f"Anthropic call failed: {e}")
        # Extract text from the response
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""


class OpenAIProvider(AIProvider):
    """OpenAI-compatible provider. Works with OpenAI itself plus any
    service that mimics the OpenAI Chat Completions API.

    When constructed via get_provider() it reads ai_chat_* settings.
    When constructed via get_embed_provider() it reads ai_embed_* settings.
    The mode is set by the constructor argument."""
    name = "openai"

    def __init__(self, mode="chat"):
        self.mode = mode
        prefix = "ai_chat_" if mode == "chat" else "ai_embed_"
        default_model = DEFAULT_MODELS["openai"] if mode == "chat" else DEFAULT_EMBED_MODELS["openai"]
        self.api_key = settings.get(prefix + "api_key", "")
        self.model = settings.get(prefix + "model", "") or default_model
        self.base_url = settings.get(prefix + "base_url", "") or DEFAULT_BASE_URLS["openai"]

    def embed(self, text):
        if not OPENAI_AVAILABLE:
            raise AIError("openai SDK not installed. Run: pip install openai")
        if not self.api_key:
            raise AIError("API key not configured. Set it in /settings.")
        client = _openai_sdk.OpenAI(api_key=self.api_key, base_url=self.base_url)
        try:
            resp = client.embeddings.create(model=self.model, input=text)
        except Exception as e:
            raise AIError(f"OpenAI-compatible embed failed: {e}")
        try:
            return list(resp.data[0].embedding)
        except (IndexError, AttributeError) as e:
            raise AIError(f"Unexpected embed response shape: {e}")

    def chat(self, messages, max_tokens=1024):
        if not OPENAI_AVAILABLE:
            raise AIError("openai SDK not installed. Run: pip install openai")
        if not self.api_key:
            raise AIError("API key not configured. Set it in /settings.")
        client = _openai_sdk.OpenAI(api_key=self.api_key, base_url=self.base_url)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
            )
        except Exception as e:
            raise AIError(f"OpenAI-compatible call failed: {e}")
        try:
            return resp.choices[0].message.content or ""
        except (IndexError, AttributeError) as e:
            raise AIError(f"Unexpected response shape: {e}")


class GeminiProvider(AIProvider):
    """Google Gemini via the google-genai SDK. Supports both chat and embeddings."""
    name = "gemini"

    def __init__(self, mode="chat"):
        self.mode = mode
        prefix = "ai_chat_" if mode == "chat" else "ai_embed_"
        default_model = DEFAULT_MODELS["gemini"] if mode == "chat" else DEFAULT_EMBED_MODELS["gemini"]
        self.api_key = (settings.get(prefix + "api_key", "") or "").strip()
        self.model = _normalize_model_name(
            settings.get(prefix + "model", ""),
            default=default_model,
        )

    def chat(self, messages, max_tokens=1024):
        if not GEMINI_AVAILABLE:
            raise AIError("google-genai SDK not installed. Run: pip install google-genai")
        if not self.api_key:
            raise AIError("Gemini API key not configured. Set it in /settings.")
        _raise_if_retired("gemini", self.model)
        client = _genai_sdk.Client(api_key=self.api_key)
        # Map our messages format to Gemini's. For single-turn (test_connection
        # and most note interrogation calls in the current build), pass the
        # last user message as a string. For multi-turn, build a Content list.
        try:
            from google.genai import types as _genai_types
        except ImportError:
            _genai_types = None
        if len(messages) == 1 and messages[0].get("role") == "user":
            contents = messages[0].get("content", "")
        else:
            # Multi-turn: build a list of Content objects
            contents = []
            for m in messages:
                role = "user" if m.get("role") == "user" else "model"
                text = m.get("content", "")
                if _genai_types:
                    contents.append(_genai_types.Content(
                        role=role,
                        parts=[_genai_types.Part(text=text)],
                    ))
                else:
                    contents.append({"role": role, "parts": [{"text": text}]})
        config = None
        if _genai_types:
            try:
                config = _genai_types.GenerateContentConfig(max_output_tokens=max_tokens)
            except Exception:
                config = None
        try:
            if config is not None:
                resp = client.models.generate_content(model=self.model, contents=contents, config=config)
            else:
                resp = client.models.generate_content(model=self.model, contents=contents)
        except Exception as e:
            msg = str(e)
            if "unexpected model name format" in msg.lower() or "invalid_argument" in msg.lower():
                raise AIError(
                    f"Gemini rejected the model name '{self.model}'. "
                    f"Check Settings → chat provider → Model for "
                    f"whitespace, typos, or an invalid model name. Common "
                    f"valid names: gemini-flash-latest (always the newest "
                    f"Flash model). (Raw error: {msg[:200]})"
                )
            raise AIError(f"Gemini call failed: {msg}")
        return getattr(resp, "text", "") or ""

    def embed(self, text):
        if not GEMINI_AVAILABLE:
            raise AIError("google-genai SDK not installed. Run: pip install google-genai")
        if not self.api_key:
            raise AIError("Gemini API key not configured. Set it in /settings.")
        _raise_if_retired("gemini", self.model)
        client = _genai_sdk.Client(api_key=self.api_key)
        try:
            resp = client.models.embed_content(model=self.model, contents=text)
        except Exception as e:
            msg = str(e)
            if "unexpected model name format" in msg.lower() or "invalid_argument" in msg.lower():
                raise AIError(
                    f"Gemini rejected the embed model name '{self.model}'. "
                    f"Check Settings → embed provider → Model for "
                    f"whitespace or typos. Common valid names: "
                    f"gemini-embedding-2, gemini-embedding-001. "
                    f"(Raw error: {msg[:200]})"
                )
            raise AIError(f"Gemini embed failed: {msg}")
        try:
            return list(resp.embeddings[0].values)
        except (AttributeError, IndexError) as e:
            raise AIError(f"Unexpected Gemini embed response: {e}")


class OllamaProvider(AIProvider):
    """Local Ollama via HTTP. No SDK needed."""
    name = "ollama"

    def __init__(self, mode="chat"):
        self.mode = mode
        prefix = "ai_chat_" if mode == "chat" else "ai_embed_"
        default_model = DEFAULT_MODELS["ollama"] if mode == "chat" else DEFAULT_EMBED_MODELS["ollama"]
        self.base_url = _normalize_base_url(
            settings.get(prefix + "base_url", ""),
            default=DEFAULT_BASE_URLS["ollama"],
        )
        self.model = _normalize_model_name(
            settings.get(prefix + "model", ""),
            default=default_model,
        )

    def _ollama_error_hint(self, code, err_body):
        """Turn an Ollama error response into an actionable message."""
        low = err_body.lower()
        if "not found" in low or "try pulling" in low or "no such" in low:
            return (
                f"Ollama model '{self.model}' not found. "
                f"Pull it first: `ollama pull {self.model}`. "
                f"Or check the model name in Settings → "
                f"{'embed provider' if self.mode == 'embed' else 'chat provider'} "
                f"→ Model."
            )
        return f"Ollama HTTP {code}: {err_body.strip()[:300]}"

    def embed(self, text):
        body = {"model": self.model, "prompt": text}
        req = Request(
            f"{self.base_url}/api/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise AIError(self._ollama_error_hint(e.code, err))
        except URLError as e:
            raise AIError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Is the Ollama server running? (Run: ollama serve) — {e.reason}"
            )
        try:
            return list(data["embedding"])
        except (KeyError, TypeError) as e:
            raise AIError(
                f"Unexpected Ollama embed response. The model '{self.model}' "
                f"may not be an embedding model — for embeddings, try "
                f"nomic-embed-text or another dedicated embed model. "
                f"(Raw: {e})"
            )

    def chat(self, messages, max_tokens=1024):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # Keep the model loaded in RAM for 10 minutes after the call so
            # subsequent turns in the same conversation don't pay cold-start
            # cost. Ollama's default is 5 min; we bump it to 10 for chat.
            "keep_alive": "10m",
            "options": {"num_predict": max_tokens},
        }
        req = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise AIError(self._ollama_error_hint(e.code, err))
        except URLError as e:
            raise AIError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Is the Ollama server running? (Run: ollama serve) — {e.reason}"
            )
        except Exception as e:
            raise AIError(f"Ollama call failed: {e}")
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise AIError(f"Unexpected Ollama response: {e}")

    def chat_stream(self, messages, max_tokens=1024):
        """Generator yielding content chunks from Ollama's streaming chat
        endpoint. Each yield is a string containing a fragment of the
        response (typically a token or two). The generator finishes when
        Ollama sends a message with done=true.

        The keep_alive flag keeps the model loaded between turns so
        consecutive requests don't pay cold-start latency."""
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": "10m",
            "options": {"num_predict": max_tokens},
        }
        req = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            # Long timeout because we're streaming a potentially long
            # response. Individual chunks arrive frequently so the socket
            # doesn't actually go idle for 300s under normal conditions.
            resp = urlopen(req, timeout=300)
        except HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise AIError(self._ollama_error_hint(e.code, err))
        except URLError as e:
            raise AIError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Is the Ollama server running? (Run: ollama serve) — {e.reason}"
            )

        try:
            for line in resp:
                if not line or not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    # Skip malformed lines rather than crashing the stream
                    continue
                chunk = (data.get("message") or {}).get("content") or ""
                if chunk:
                    yield chunk
                if data.get("done"):
                    return
        finally:
            try:
                resp.close()
            except Exception:
                pass


class VoyageProvider(AIProvider):
    """Voyage AI embeddings via HTTP. Anthropic's recommended embeddings
    partner. Embeddings-only — no chat() method because Voyage doesn't
    offer chat models."""
    name = "voyage"

    def __init__(self, mode="embed"):
        if mode != "embed":
            raise AIError(
                "Voyage AI is an embeddings-only provider. "
                "Use a different provider (Anthropic, OpenAI, Gemini, or "
                "Ollama) for chat."
            )
        self.mode = mode
        prefix = "ai_embed_"
        self.api_key = (settings.get(prefix + "api_key", "") or "").strip()
        self.base_url = _normalize_base_url(
            settings.get(prefix + "base_url", ""),
            default=DEFAULT_BASE_URLS["voyage"],
        )
        self.model = _normalize_model_name(
            settings.get(prefix + "model", ""),
            default=DEFAULT_EMBED_MODELS["voyage"],
        )
        if not self.api_key:
            raise AIError(
                "Voyage AI is selected as the embedding provider but no "
                "API key is set. Get one at voyageai.com and paste it into "
                "Settings → Embedding provider."
            )

    def chat(self, messages, max_tokens=1024):
        raise AIError("Voyage AI does not support chat. Use a different provider for interrogation.")

    def embed(self, text):
        body = {
            "input": [text],
            "model": self.model,
            "input_type": "document",
        }
        req = Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace").strip()
            if e.code == 401:
                raise AIError(
                    "Voyage AI rejected the API key. Check it in Settings "
                    "→ Embedding provider."
                )
            if e.code == 404:
                raise AIError(
                    f"Voyage AI model '{self.model}' not found. "
                    f"Try 'voyage-3.5' or see voyageai.com/docs for the "
                    f"current model list."
                )
            raise AIError(f"Voyage HTTP {e.code}: {err_body[:200]}")
        except URLError as e:
            raise AIError(
                f"Cannot reach Voyage AI at {self.base_url}. "
                f"Check your network connection — {e.reason}"
            )
        try:
            return list(data["data"][0]["embedding"])
        except (KeyError, IndexError, TypeError) as e:
            raise AIError(f"Unexpected Voyage response: {e}")


def get_provider():
    """Return a configured AIProvider, or None if no provider is set."""
    name = settings.get("ai_chat_provider", "") or ""
    name = name.strip().lower()
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return OpenAIProvider()
    if name == "gemini":
        return GeminiProvider()
    if name == "ollama":
        return OllamaProvider()
    return None


def get_embed_provider():
    """Return a configured embedding provider, or None. Reads ai_embed_*
    settings (separate from chat config because Anthropic has no embeddings
    of its own — their recommended partner for that is Voyage AI)."""
    name = (settings.get("ai_embed_provider", "") or "").strip().lower()
    if name == "openai":
        return OpenAIProvider(mode="embed")
    if name == "gemini":
        return GeminiProvider(mode="embed")
    if name == "ollama":
        return OllamaProvider(mode="embed")
    if name == "voyage":
        return VoyageProvider(mode="embed")
    return None


def provider_status():
    """Return a dict describing current provider config and SDK availability."""
    return {
        "current": settings.get("ai_chat_provider", "") or "none",
        "current_embed": settings.get("ai_embed_provider", "") or "none",
        "anthropic_sdk": ANTHROPIC_AVAILABLE,
        "openai_sdk": OPENAI_AVAILABLE,
        "gemini_sdk": GEMINI_AVAILABLE,
        "ollama": True,
    }
