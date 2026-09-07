"""
JARVIS Prompts

System prompts, templates, and prompt construction utilities.
These define JARVIS's personality, capabilities, and response format.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Core System Prompt
# ---------------------------------------------------------------------------

JARVIS_SYSTEM_PROMPT = """You are a helpful assistant with a calm, direct, masculine-leaning tone.
Speak naturally, like a real person having a conversation.
Use short sentences and everyday words.
Be practical, confident, and a little relaxed.
Avoid overly formal language, filler phrases, and robotic wording.
Never use stiff honorifics like "sir" or "boss."
When the user gives a command, answer clearly and directly.
Keep responses grounded in daily life examples when useful.
Do not sound aggressive, rude, or disrespectful.
Do not mention that you are trying to sound like a man.
Use natural contractions like "I'm," "you're," "that's," "let's."
Do not over-explain unless asked. Give one clear, practical answer instead of ten options.

## Daily-Life Style & Examples:
- When asked "Make a grocery list for the week": Give a straightforward, sensible list of everyday staples and dinners.
- When asked "Plan my morning routine": Give a simple, realistic routine that gets someone moving without overcomplicating things.
- When asked "Tell me the easiest way to fix this": Give the single fastest, most practical fix first.
- When asked "Keep it simple and practical" or "Give one clear answer, not ten options": Cut straight to the point without fluff.

## Safety & Action Rules:
- Answer clearly, directly, and honestly.
- Never claim an action was done unless it succeeded.
- Keep spoken responses under 2-3 short, natural sentences.
"""


# ---------------------------------------------------------------------------
# Intent Classification Prompt
# ---------------------------------------------------------------------------

INTENT_CLASSIFICATION_PROMPT = """Classify the user's input into one of these intents:

{intents}

Respond with ONLY a JSON object in this exact format:
{{
  "intent": "<intent_name>",
  "entities": {{}},
  "confidence": <0.0 to 1.0>,
  "risk_level": "none|low|medium|high",
  "requires_confirmation": <true|false>
}}

Rules:
- If the input is a general question or conversation, use "conversation".
- If the input is unclear, use "clarification_needed" with low confidence.
- Risk levels: none (info), low (timers, apps), medium (files, browser), high (send, delete).
- Always require confirmation for send, delete, purchase, or security actions.
- Extract entities like: app_name, file_path, time, date, contact, query, amount, etc.
- Be conservative: if unsure, set confidence below 0.7.

Current time: {current_time}
"""


# ---------------------------------------------------------------------------
# Available Intents (for classification prompt)
# ---------------------------------------------------------------------------

INTENTS = """
- conversation: General conversation, questions, or chat
- clarification_needed: Input is ambiguous or unclear
- time_query: Asking for current time or date
- set_timer: Setting a countdown timer
- set_reminder: Setting a reminder for a future time
- open_app: Opening a desktop application
- close_app: Closing a desktop application
- search_web: Searching the internet for information
- file_search: Finding files on the computer
- file_read: Reading a file's contents
- file_write: Creating or modifying a file
- file_delete: Deleting a file (always confirm)
- calculator: Mathematical calculations
- system_status: Checking system resources (CPU, memory, etc.)
- email_draft: Drafting an email
- email_send: Sending an email (always confirm)
- browser_open: Opening a website in the browser
- notes_create: Creating a note
- notes_read: Reading notes
- workflow_create: Creating a multi-step routine
- workflow_run: Running a saved routine
- emergency_stop: Stopping all current operations
- memory_store: Storing information for later recall
- memory_recall: Recalling stored information
- memory_forget: Forgetting stored information
"""


# ---------------------------------------------------------------------------
# Response Templates
# ---------------------------------------------------------------------------

CLARIFICATION_TEMPLATE = "I'm not quite sure what you mean. Could you rephrase that?"

CONFIRMATION_TEMPLATE = """I need your confirmation before proceeding.

**Action:** {action}
**Details:** {details}
**Risk Level:** {risk_level}

Should I proceed? Reply "yes" to confirm or "no" to cancel."""

ERROR_TEMPLATE = "I encountered an issue: {error}. Would you like me to try a different approach?"

SUCCESS_TEMPLATE = "{action} completed successfully. {result}"

OFFLINE_TEMPLATE = (
    "I'm currently offline. I can help with local tasks like timers, "
    "calculations, and file operations. For web-based features, "
    "please check your internet connection."
)


# ---------------------------------------------------------------------------
# Prompt Construction Utilities
# ---------------------------------------------------------------------------

def build_system_prompt(
    custom_instructions: str | None = None,
    user_preferences: str | None = None,
    current_context: str | None = None,
) -> str:
    """
    Build a complete system prompt with optional additions.

    Args:
        custom_instructions: Additional instructions for this session.
        user_preferences: Known user preferences.
        current_context: Current context (location, time, etc.).

    Returns:
        Complete system prompt string.
    """
    parts = [JARVIS_SYSTEM_PROMPT]

    if user_preferences:
        parts.append(f"\n## User Preferences\n{user_preferences}")

    if current_context:
        parts.append(f"\n## Current Context\n{current_context}")

    if custom_instructions:
        parts.append(f"\n## Additional Instructions\n{custom_instructions}")

    return "\n".join(parts)


def build_intent_prompt(
    user_input: str,
    current_time: str,
) -> str:
    """
    Build the intent classification prompt.

    Args:
        user_input: The user's raw input text.
        current_time: Current timestamp string.

    Returns:
        Formatted intent classification prompt.
    """
    return INTENT_CLASSIFICATION_PROMPT.format(
        intents=INTENTS.strip(),
        current_time=current_time,
    )


def format_confirmation_message(
    action: str,
    details: str,
    risk_level: str = "medium",
) -> str:
    """
    Format a confirmation request message.

    Args:
        action: Description of the action.
        details: Detailed information.
        risk_level: Risk level of the action.

    Returns:
        Formatted confirmation message.
    """
    return CONFIRMATION_TEMPLATE.format(
        action=action,
        details=details,
        risk_level=risk_level,
    )
