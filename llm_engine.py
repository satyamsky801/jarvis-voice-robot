"""
JARVIS Advanced LLM Engine
Provides deep human language understanding, conversational intelligence,
multi-turn memory, action extraction, and multi-provider support (Ollama, Groq, Gemini, OpenAI).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Default Persona Prompt tailored for spoken interaction
DEFAULT_SYSTEM_PROMPT = """You are Jarvis, an advanced AI assistant with a calm, direct, masculine-leaning tone.
Speak naturally, like a real person having a conversation.
Use short sentences and everyday words from daily life.
Be practical, confident, and relaxed.
Never use stiff honorifics like "sir" or "boss."
Do not sound robotic, overly formal, or repetitive.
Use natural contractions like "I'm," "you're," "that's," "let's."
Do not over-explain. Give one clear, practical answer.

ACTIONS:
You have direct control of the user's Windows PC. When the user asks to perform a computer action, output a JSON object:
{
  "action": "<ACTION_NAME>",
  "action_param": "<PARAMETER_OR_NULL>",
  "speech": "<SHORT_SPOKEN_CONFIRMATION>"
}

Supported actions:
- "search_youtube": parameter is search query (e.g. "lofi beats", "python tutorial")
- "play_youtube": parameter is song or video query
- "open_app": parameter is application name (e.g. "chrome", "notepad", "spotify", "calculator", "terminal")
- "close_app": parameter is application name (e.g. "edge", "chrome", "spotify")
- "screenshot": parameter is null
- "volume_up": parameter is null
- "volume_down": parameter is null
- "mute": parameter is null
- "pause_media": parameter is null
- "resume_media": parameter is null
- "next_track": parameter is null
- "previous_track": parameter is null
- "open_website": parameter is full URL or domain (e.g. "https://github.com")

If the user is having a conversation, asking a question, asking for recommendations or advice, or brainstorming:
CRITICAL: Set "action" to null and "action_param" to null! Only use actions when the user explicitly commands you to control their computer or open apps.
Put your direct, helpful answer in "speech". Keep "speech" to 1-3 natural spoken sentences.
Always return valid JSON only.
"""


def clean_for_speech(text: str) -> str:
    """Clean text of markdown, code blocks, bullet points, and URLs for natural TTS."""
    if not text:
        return ""

    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]*`', '', text)

    # Remove markdown links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # Remove raw URLs
    text = re.sub(r'https?://\S+', '', text)

    # Remove markdown headers (# Header)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

    # Strip asterisks, underscores, and formatting chars
    text = text.replace('**', ' ').replace('*', ' ').replace('__', ' ').replace('_', ' ')

    # Remove list bullets (*, -, •, 1.)
    text = re.sub(r'(?:^|\n|\s+)[-*•]\s+', ' ', text)
    text = re.sub(r'(?:^|\n)\s*\d+\.\s+', ' ', text)

    # Remove brackets from action tags if any leaked
    text = re.sub(r'\[ACTION:[^\]]+\]', '', text, flags=re.IGNORECASE)

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


@dataclass
class LLMResult:
    """Parsed result from LLM reasoning."""
    speech: str
    action: Optional[str] = None
    action_param: Optional[str] = None
    raw_response: str = ""
    provider: str = ""
    model: str = ""


class JarvisLLMEngine:
    """
    Advanced LLM Engine supporting Ollama (local), Groq, Gemini, and OpenAI.
    Maintains multi-turn conversational context and parses natural speech intent.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_history: int = 10,
    ):
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.max_history = max_history
        self.history: list[dict[str, str]] = []

        # Detect provider and model
        self.provider, self.model, self.base_url, self.api_key = self._detect_provider(
            provider, model, base_url
        )
        logger.info("JarvisLLMEngine initialized with provider: %s, model: %s", self.provider, self.model)

    def _detect_provider(
        self,
        req_provider: Optional[str],
        req_model: Optional[str],
        req_url: Optional[str]
    ) -> Tuple[str, str, str, str]:
        """Detect and configure the best available LLM provider."""
        # 1. Check explicit override
        env_provider = os.getenv("JARVIS_LLM_PROVIDER", "").lower()
        active_provider = (req_provider or env_provider or "").strip()

        # Check API keys
        groq_key = os.getenv("GROQ_API_KEY", "")
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if openai_key.startswith("REPLACE_ME"):
            openai_key = ""

        # Default Ollama config
        ollama_url = req_url or os.getenv("JARVIS_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ollama_model = req_model or os.getenv("JARVIS_OLLAMA_MODEL", "llama3.2")

        if active_provider == "groq" and groq_key:
            return "groq", req_model or "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1", groq_key
        elif active_provider == "gemini" and gemini_key:
            return "gemini", req_model or "gemini-2.0-flash", "https://generativelanguage.googleapis.com", gemini_key
        elif active_provider == "openai" and openai_key:
            return "openai", req_model or "gpt-4o", "https://api.openai.com/v1", openai_key
        elif active_provider == "ollama":
            return "ollama", ollama_model, ollama_url, ""

        # Auto-detect if not specified or ollama is accessible
        if self._is_ollama_live(ollama_url):
            return "ollama", ollama_model, ollama_url, ""

        if groq_key:
            return "groq", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1", groq_key
        if gemini_key:
            return "gemini", "gemini-2.0-flash", "https://generativelanguage.googleapis.com", gemini_key
        if openai_key:
            return "openai", "gpt-4o", "https://api.openai.com/v1", openai_key

        # Fallback to Ollama localhost
        return "ollama", ollama_model, ollama_url, ""

    def _is_ollama_live(self, url: str) -> bool:
        """Check if local Ollama server is responding."""
        try:
            req = urllib.request.Request(f"{url.rstrip('/')}/api/tags", headers={"User-Agent": "Jarvis/1.0"})
            with urllib.request.urlopen(req, timeout=1.5) as r:
                return r.status == 200
        except Exception:
            return False

    def clear_history(self):
        """Reset conversation context."""
        self.history.clear()

    def process_human_language(self, user_query: str) -> LLMResult:
        """
        Process natural human language query using the active LLM.
        Maintains conversation history and extracts action intents.
        """
        clean_query = user_query.strip()
        if not clean_query:
            return LLMResult(speech="I'm listening. What do you need?", provider=self.provider, model=self.model)

        # Handle explicit context reset commands
        if clean_query.lower() in ["clear memory", "forget what we said", "new conversation", "reset context"]:
            self.clear_history()
            return LLMResult(speech="Memory cleared. Starting fresh.", provider=self.provider, model=self.model)

        # Build message payload with rolling history
        messages = [{"role": "system", "content": self.system_prompt}]
        for turn in self.history[-self.max_history:]:
            messages.append(turn)
        messages.append({"role": "user", "content": clean_query})

        # Query active provider
        raw_text = ""
        try:
            if self.provider == "ollama":
                raw_text = self._query_ollama(messages)
            elif self.provider == "groq":
                raw_text = self._query_openai_compatible(messages, self.base_url, self.api_key, self.model)
            elif self.provider == "openai":
                raw_text = self._query_openai_compatible(messages, self.base_url, self.api_key, self.model)
            elif self.provider == "gemini":
                raw_text = self._query_gemini(messages, self.api_key, self.model)
            else:
                raw_text = self._query_ollama(messages)
        except Exception as e:
            logger.error("LLM generation error: %s", e)
            # Try Ollama fallback if cloud failed
            if self.provider != "ollama" and self._is_ollama_live(self.base_url):
                try:
                    raw_text = self._query_ollama(messages)
                except Exception:
                    pass

        # If LLM completely failed, provide casual fallback
        if not raw_text:
            return LLMResult(
                speech="I got you, but had a slight hiccup reaching my model. What can I do for you?",
                provider=self.provider,
                model=self.model
            )

        # Parse JSON or structured response
        result = self._parse_llm_response(raw_text, user_query=clean_query)

        # Update history with user query and assistant speech
        self.history.append({"role": "user", "content": clean_query})
        self.history.append({"role": "assistant", "content": result.speech})
        if len(self.history) > self.max_history * 2:
            self.history = self.history[-self.max_history * 2:]

        return result

    def _query_ollama(self, messages: list[dict[str, str]]) -> str:
        """Query local Ollama chat API with JSON mode."""
        endpoint = f"{self.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.3,
                "top_p": 0.9,
            }
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "JarvisRobot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")

    def _query_openai_compatible(
        self,
        messages: list[dict[str, str]],
        base_url: str,
        api_key: str,
        model: str
    ) -> str:
        """Query OpenAI or Groq API."""
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "response_format": {"type": "json_object"}
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "JarvisRobot/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    def _query_gemini(
        self,
        messages: list[dict[str, str]],
        api_key: str,
        model: str
    ) -> str:
        """Query Google Gemini API."""
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        contents = []
        for m in messages:
            role = "user" if m["role"] in ["user", "system"] else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3
            }
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"]

    def _parse_llm_response(self, raw_text: str, user_query: str = "") -> LLMResult:
        """Extract action, action_param, and speech text from LLM response."""
        raw_clean = raw_text.strip()
        speech = ""
        action = None
        action_param = None

        # 1. Try JSON parsing
        try:
            json_match = re.search(r'\{[\s\S]*\}', raw_clean)
            if json_match:
                parsed = json.loads(json_match.group(0))
                speech = parsed.get("speech", "")
                raw_act = parsed.get("action")
                if raw_act and str(raw_act).lower() not in ["null", "none", ""]:
                    action = str(raw_act).strip().lower()
                    raw_param = parsed.get("action_param")
                    if raw_param and str(raw_param).lower() not in ["null", "none", ""]:
                        action_param = str(raw_param).strip()
        except Exception:
            pass

        # 2. Fallback to Action Tag parsing if not valid JSON
        if not speech:
            act_match = re.search(r'\[ACTION:\s*([a-zA-Z0-9_-]+)(?::\s*([^\]]+))?\]', raw_clean, re.IGNORECASE)
            if act_match:
                action = act_match.group(1).lower().strip()
                if act_match.group(2):
                    action_param = act_match.group(2).strip()
                speech = re.sub(r'\[ACTION:[^\]]+\]', '', raw_clean, flags=re.IGNORECASE).strip()

        # 3. Validate that extracted action matches user query intent (prevent false positives)
        if action and user_query:
            q_lower = user_query.lower()
            valid = False
            if action in ["search_youtube", "play_youtube"]:
                valid = any(w in q_lower for w in ["youtube", "yt", "video", "song", "music", "play", "watch", "listen", "track", "search"])
            elif action == "open_app":
                valid = any(w in q_lower for w in ["open", "launch", "start", "run", "bring up", "app", "application"])
            elif action == "close_app":
                valid = any(w in q_lower for w in ["close", "kill", "terminate", "exit", "shut down", "stop"])
            elif action == "screenshot":
                valid = any(w in q_lower for w in ["screenshot", "screen", "picture of", "capture", "snapshot"])
            elif action in ["volume_up", "volume_down", "mute"]:
                valid = any(w in q_lower for w in ["volume", "sound", "audio", "mute", "unmute", "louder", "quieter"])
            elif action in ["pause_media", "resume_media", "next_track", "previous_track"]:
                valid = any(w in q_lower for w in ["pause", "resume", "stop", "next", "previous", "skip", "play"])
            elif action == "open_website":
                valid = any(w in q_lower for w in ["open", "browse", "go to", "website", "site", "page", "http", ".com", ".org", ".io"])

            if not valid:
                action = None
                action_param = None

        # 4. If speech is still empty, use cleaned raw text
        if not speech:
            speech = raw_clean

        # Sanitize speech text for TTS
        cleaned_speech = clean_for_speech(speech)
        if not cleaned_speech:
            cleaned_speech = "Got it."

        return LLMResult(
            speech=cleaned_speech,
            action=action,
            action_param=action_param,
            raw_response=raw_clean,
            provider=self.provider,
            model=self.model
        )
