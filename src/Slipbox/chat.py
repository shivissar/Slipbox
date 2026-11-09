"""
chat.py — Note interrogation chat (M4.3) and tag suggestion (M4.4).

The conversation state for note interrogation is held in an in-memory dict
keyed by note_id. This is DELIBERATELY ephemeral — wiped on every process
restart. The philosophy of the project is that if a chat insight matters,
the user writes it as their own note in their own words. The chat is an
interlocutor, not a journal.

Both features call ai.get_provider().chat(messages) under the hood, so
they work with any of the four configured chat providers (Anthropic,
OpenAI-compatible, Gemini, Ollama).
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import threading

from . import ai


# ---------- Conversation state ---------- #

# {note_id: [{"role": "user"|"assistant", "content": "..."}]}
_conversations = {}
_lock = threading.Lock()


def get_conversation(note_id):
    with _lock:
        return list(_conversations.get(note_id, []))


def append_message(note_id, role, content):
    with _lock:
        _conversations.setdefault(note_id, []).append({"role": role, "content": content})


def clear_conversation(note_id):
    with _lock:
        _conversations.pop(note_id, None)


def conversation_length(note_id):
    with _lock:
        return len(_conversations.get(note_id, []))


# ---------- System prompts ---------- #

INTERROGATION_SYSTEM_PROMPT = """You are a research assistant helping a scholar think through their atomic notes. Your job is to ask probing questions, identify hidden assumptions, surface counterarguments, suggest disconfirming evidence, and help the user develop their thinking.

Strict rules:
- Do NOT rewrite the note for them.
- Do NOT summarize the note back to them — they wrote it, they know what it says.
- Do NOT suggest specific text edits.
- Do NOT be sycophantic. Skip "great note!" and similar.
- Engage Socratically: your goal is to help them think more clearly, not to think for them.
- When you make a substantive critique, be specific. Vague encouragement is worse than nothing.
- Be concise. Long responses dilute the critique.
- If the note's argument has an obvious weakness, name it directly.

The user is a scholar in sociology / STS, working in atomic-note (Zettelkasten) tradition. Treat them as a peer."""


TAG_SUGGESTION_SYSTEM_PROMPT = """You are a tag suggestion assistant for a Zettelkasten. The user will give you:
1. The title and body of a note
2. A list of existing tags they use in their corpus

Your job: suggest 3-5 tags from the existing list that best describe this note.

Strict rules:
- You MUST ONLY suggest tags from the provided list. Do NOT invent new tags.
- Return ONLY the tag names, one per line, no other text. No explanations, no preamble, no markdown.
- If fewer than 3 tags from the list fit well, return fewer. Quality over quantity.
- If no tags fit, return nothing (empty response)."""


# ---------- Note interrogation ---------- #


def build_note_context_message(note):
    """Construct the user-facing 'system context' that's prepended to the
    first user message, so the AI knows what note is being discussed."""
    from . import export  # 5.2: embedded notes are written out (never locked ones)
    tags_str = ", ".join(note.tags) if note.tags else "(none)"
    body = export.text_for_ai(note.body, seen={note.id})
    return f"""I'm thinking about the following note from my Zettelkasten. Please help me interrogate it.

Title: {note.title}
Tags: {tags_str}

---

{body}

---

Please respond to my question or remark below."""


def _build_messages(note, user_message):
    """Construct the messages list for either streaming or blocking calls.
    On the first turn we fold the system prompt + note context into the
    first user message (portable across providers). On subsequent turns
    we just append the new user message to existing history."""
    history = get_conversation(note.id)
    if not history:
        first_user = (
            INTERROGATION_SYSTEM_PROMPT
            + "\n\n"
            + build_note_context_message(note)
            + "\n\n"
            + user_message
        )
        return [{"role": "user", "content": first_user}]
    return list(history) + [{"role": "user", "content": user_message}]


def stream_ask(note, user_message):
    """Generator yielding (kind, payload) tuples where kind is either
    'chunk' (with a content string) or 'error' (with an error string)
    or 'done' (no payload). After the generator finishes yielding,
    the complete turn has been committed to conversation history.

    Providers that implement chat_stream() (currently Ollama) stream
    tokens as they arrive. Providers that don't (everything else) fall
    back to a single chunk containing the full response, so the client
    code path is the same for both cases."""
    provider = ai.get_provider()
    if not provider:
        yield "error", "No AI chat provider configured. Configure one in /settings."
        return

    messages = _build_messages(note, user_message)

    stream_method = getattr(provider, "chat_stream", None)
    accumulated = []

    if stream_method is not None:
        try:
            for chunk in stream_method(messages, max_tokens=1024):
                accumulated.append(chunk)
                yield "chunk", chunk
        except ai.AIError as e:
            yield "error", str(e)
            return
        except Exception as e:
            yield "error", f"Unexpected error: {e}"
            return
    else:
        # Fall back: blocking call, return entire response as one chunk
        try:
            response = provider.chat(messages, max_tokens=1024)
        except ai.AIError as e:
            yield "error", str(e)
            return
        except Exception as e:
            yield "error", f"Unexpected error: {e}"
            return
        accumulated.append(response)
        yield "chunk", response

    full_response = "".join(accumulated)
    append_message(note.id, "user", user_message)
    append_message(note.id, "assistant", full_response)
    yield "done", ""


def ask(note, user_message):
    """Send a user message about `note` to the configured chat provider.
    Returns (ok: bool, response_text: str). Appends to conversation history.

    Kept for backward compatibility. New clients should use stream_ask()
    which supports both streaming and non-streaming providers via a
    uniform generator interface."""
    provider = ai.get_provider()
    if not provider:
        return False, "No AI chat provider configured. Configure one in /settings."

    messages = _build_messages(note, user_message)

    try:
        response = provider.chat(messages, max_tokens=1024)
    except ai.AIError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error: {e}"

    # Persist this turn in the conversation history
    append_message(note.id, "user", user_message)
    append_message(note.id, "assistant", response)
    return True, response


# ---------- Tag suggestions ---------- #


def suggest_tags(title, body, existing_tags):
    """Ask the AI to suggest 3-5 tags from `existing_tags` for the given
    note. Returns (ok: bool, tags: list[str], message: str). Tags not in
    the existing vocabulary are silently filtered out."""
    if not existing_tags:
        return False, [], "No existing tags to choose from. Add some tags to other notes first."

    provider = ai.get_provider()
    if not provider:
        return False, [], "No AI chat provider configured. Configure one in /settings."

    # Sort existing tags so the prompt is deterministic
    sorted_tags = sorted(set(existing_tags))
    tag_list_str = "\n".join(f"- {t}" for t in sorted_tags)

    user_prompt = (
        TAG_SUGGESTION_SYSTEM_PROMPT
        + "\n\n"
        + f"Existing tags in the corpus:\n{tag_list_str}\n\n"
        + f"Note title: {title}\n\n"
        + f"Note body:\n{body[:4000]}\n\n"  # cap to avoid runaway costs
        + "Suggest 3-5 tags from the list above. Return only tag names, one per line."
    )

    try:
        response = provider.chat([{"role": "user", "content": user_prompt}], max_tokens=200)
    except ai.AIError as e:
        return False, [], str(e)
    except Exception as e:
        return False, [], f"Unexpected error: {e}"

    # Parse the response: split by newlines, strip whitespace, strip leading
    # bullets/dashes/numbers, filter to existing tags only.
    tag_set = set(sorted_tags)
    raw_lines = [line.strip() for line in response.split("\n") if line.strip()]
    suggestions = []
    for line in raw_lines:
        # Strip common bullet/numbering prefixes
        cleaned = line.lstrip("-*•·0123456789. )(").strip()
        # Strip backticks, quotes
        cleaned = cleaned.strip("`'\"")
        if cleaned in tag_set and cleaned not in suggestions:
            suggestions.append(cleaned)
    return True, suggestions[:5], "ok"
