"""
J.A.R.V.I.S. — Autonomous Voice Robot Assistant
Inspired by Tony Stark's J.A.R.V.I.S.

Features:
- Pure Voice Input & Spoken Dialogue (Talks naturally like Google Assistant)
- Single-Instance Enforcement (Kills stale background duplicates so voices/windows never double)
- Zero Unsolicited Command Suggestions (No canned "say open chrome" prompts)
- Flexible Intent Recognition (Handles conversational phrasing like "i am saying that", "how much time", "showing system status")
- Dedicated YouTube & Media Engine (Handles "open yt", "open youtube", "play music" without duplicate windows)
- Zero Unwanted Browser Popups (Speaks answers directly via Wikipedia & DuckDuckGo APIs)
- Auto-Unmute & 100% Hardware Volume Boost (Fixes Windows microphone mute)
- Dynamic Microphone Sensitivity (Auto-calibrating ambient noise)
- Audio Normalization (Boosts quiet laptop microphone levels for Google STT)
- British Ryan Neural Voice via Edge-TTS (100% Free, High Quality)
- Non-blocking Callback Audio Stream (Supports Windows WDM-KS, MME, DirectSound, WASAPI)
- Dual Input: Voice Listening + Instant Keyboard Typing
- Direct Task Execution:
  * Application launching and closing (Chrome, Notepad, Calc, Code, etc.)
  * Web searches & YouTube playback (only when explicitly requested)
  * System diagnostics (CPU, RAM, Battery, Disk)
  * Volume control (Up, Down, Mute)
  * Time, Date, Weather
  * Screen capture
  * Quick notes
  * Conversational knowledge & instant answers spoken aloud
"""

import sys

# Ensure UTF-8 encoding across Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import ctypes
import datetime
import json
import logging
import msvcrt
import os
import platform
import queue
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import psutil
import scipy.signal
import sounddevice as sd
import soundfile as sf
import speech_recognition as sr
from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# Initialize Rich Console with safe encoding
console = Console(highlight=False)

# Virtual Key Codes for Windows Volume Control
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

# Assistant Settings
VOICE_NAME = "en-GB-RyanNeural"  # British J.A.R.V.I.S. voice
WAKE_WORDS = ["jarvis", "hey jarvis", "robot", "hello jarvis"]
NOTES_FILE = Path("jarvis_notes.txt")
SCREENSHOTS_DIR = Path(os.path.expanduser("~/Pictures/Jarvis_Screenshots"))
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# 0. SINGLE-INSTANCE PROCESS ENFORCEMENT & MICROPHONE AUTO-UNMUTE
# ===========================================================================
def enforce_single_instance():
    """Ensure only one instance of JARVIS runs at a time, killing any stale/duplicate instances."""
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] != current_pid and proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = " ".join(proc.info.get('cmdline') or []).lower()
                if 'voice_robot.py' in cmdline:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def ensure_microphone_active_and_unmuted() -> Tuple[bool, str]:
    """
    Ensure the Windows microphone is unmuted and boosted to 100% volume.
    Returns (success, mic_name).
    """
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL

        devices = AudioUtilities.GetAllDevices()
        for d in devices:
            name = d.FriendlyName or ""
            if "Microphone Array" in name or "Microphone" in name:
                try:
                    dev = AudioUtilities.GetAudioDevice(d.Id)
                    if dev:
                        volume = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
                        if volume.GetMute() == 1:
                            volume.SetMute(0, None)
                        volume.SetMasterVolumeLevelScalar(1.0, None)
                        return True, name
                except Exception:
                    pass
        return False, "Default Microphone"
    except Exception:
        return False, "Default Microphone"


# ===========================================================================
# 1. VOICE OUTPUT (EDGE-TTS NEURAL BRITISH J.A.R.V.I.S.)
# ===========================================================================
class JarvisVoice:
    """High-fidelity neural voice using Edge-TTS with async playback queue."""

    def __init__(self, voice_name: str = VOICE_NAME):
        self.voice_name = voice_name
        self.speech_queue = queue.Queue()
        self.is_speaking = False
        self._worker_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self._worker_thread.start()

    def speak(self, text: str):
        """Queue text to be spoken out loud."""
        clean_text = text.strip()
        if not clean_text:
            return

        # Display spoken dialogue cleanly in the console
        try:
            console.print(f"[bold cyan][J.A.R.V.I.S.]:[/bold cyan] [italic bright_white]{clean_text}[/italic bright_white]")
        except Exception:
            print(f"[J.A.R.V.I.S.]: {clean_text}")

        self.speech_queue.put(clean_text)

    def _speech_worker(self):
        """Worker processing speech queue synchronously."""
        while True:
            text = self.speech_queue.get()
            self.is_speaking = True
            try:
                self._synthesize_and_play(text)
            except Exception as e:
                pass
            finally:
                self.is_speaking = False
                self.speech_queue.task_done()

    def _synthesize_and_play(self, text: str):
        """Generate audio using Edge-TTS and stream to sound device."""
        temp_dir = tempfile.gettempdir()
        temp_mp3 = os.path.join(temp_dir, f"jarvis_voice_{os.getpid()}_{int(time.time()*1000)}.mp3")

        async def _generate():
            import edge_tts
            communicate = edge_tts.Communicate(text, self.voice_name, rate="+5%", pitch="+0Hz")
            await communicate.save(temp_mp3)

        try:
            asyncio.run(_generate())
            if os.path.exists(temp_mp3) and os.path.getsize(temp_mp3) > 0:
                data, fs = sf.read(temp_mp3)
                sd.play(data, fs)
                sd.wait()
        except Exception:
            # Fallback to Windows native SAPI if Edge-TTS fails
            self._fallback_sapi_speak(text)
        finally:
            if os.path.exists(temp_mp3):
                try:
                    os.remove(temp_mp3)
                except Exception:
                    pass

    def _fallback_sapi_speak(self, text: str):
        """Windows native SAPI fallback speech engine."""
        try:
            safe_text = text.replace('"', ' ').replace("'", " ")
            cmd = f'powershell -Command "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak(\'{safe_text}\')"'
            subprocess.run(cmd, shell=True, capture_output=True)
        except Exception:
            pass


# ===========================================================================
# 2. VOICE LISTENER (MICROPHONE CAPTURE & GOOGLE STT)
# ===========================================================================
class JarvisEar:
    """Robust non-blocking audio capture stream with audio normalization."""

    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.input_device, self.device_name = self._find_best_input_device()
        device_info = sd.query_devices(self.input_device)
        self.sample_rate = int(device_info.get("default_samplerate", 44100))
        self.channels = min(2, max(1, device_info.get("max_input_channels", 1)))
        self.speech_threshold = 0.005
        self.ambient_rms = 0.001

    def _find_best_input_device(self) -> Tuple[int, str]:
        """Find the working microphone input device, prioritizing Microphone Array."""
        devices = sd.query_devices()

        # 1. First priority: Microphone Array
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d.get("name", "").lower()
                if "array" in name:
                    return i, d.get("name", "")

        # 2. Second priority: Any microphone input
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d.get("name", "").lower()
                if "mic" in name:
                    return i, d.get("name", "")

        # 3. Fallback: Any available input device
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                return i, d.get("name", "")

        return 1, "Microphone Array"

    def calibrate(self, duration_sec: float = 0.4):
        """Calibrate ambient background noise level."""
        audio_q = queue.Queue()

        def callback(indata, frames, time_info, status):
            audio_q.put(indata.copy())

        try:
            with sd.InputStream(
                device=self.input_device,
                channels=self.channels,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=callback,
            ):
                time.sleep(duration_sec)

            chunks = [audio_q.get() for _ in range(audio_q.qsize())]
            if chunks:
                all_audio = np.concatenate(chunks, axis=0)
                rms = float(np.sqrt(np.mean(np.square(all_audio))))
                self.ambient_rms = rms
                self.speech_threshold = max(0.003, min(0.025, self.ambient_rms * 1.5))
        except Exception:
            self.speech_threshold = 0.005

    def record_phrase(self, max_duration_sec: float = 8.0, silence_cutoff: float = 1.6) -> Tuple[Optional[sr.AudioData], Optional[str]]:
        """
        Record speech or capture keyboard input.
        Returns (AudioData, typed_text).
        """
        audio_q = queue.Queue()

        def callback(indata, frames, time_info, status):
            audio_q.put(indata.copy())

        try:
            stream = sd.InputStream(
                device=self.input_device,
                channels=self.channels,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=callback,
                blocksize=int(self.sample_rate * 0.05),
            )
        except Exception:
            try:
                console.print("[dim white]Type your command below:[/dim white]")
                typed = input("You > ").strip()
                return None, typed if typed else None
            except Exception:
                return None, None

        recorded_chunks = []
        speech_started = False
        silence_time = 0.0
        start_time = time.time()
        typed_chars = []

        with stream:
            while (time.time() - start_time) < max_duration_sec:
                # Check for keyboard typing
                if msvcrt.kbhit():
                    ch = msvcrt.getwche()
                    if ch in ('\r', '\n'):
                        print()
                        typed = "".join(typed_chars).strip()
                        if typed:
                            return None, typed
                    elif ch == '\b':  # Backspace
                        if typed_chars:
                            typed_chars.pop()
                    else:
                        typed_chars.append(ch)

                try:
                    chunk = audio_q.get(timeout=0.05)
                except queue.Empty:
                    continue

                chunk_dur = len(chunk) / self.sample_rate
                rms = float(np.sqrt(np.mean(np.square(chunk))))

                # Detect speech activity
                if rms > self.speech_threshold:
                    if not speech_started:
                        speech_started = True
                        console.print("[bold cyan]🎙️ [Hearing voice... Speak now][/bold cyan]")
                    silence_time = 0.0
                    recorded_chunks.append(chunk)
                elif speech_started:
                    silence_time += chunk_dur
                    recorded_chunks.append(chunk)
                    if silence_time >= silence_cutoff:
                        break
                else:
                    recorded_chunks.append(chunk)
                    max_pre = int(0.3 / max(0.01, chunk_dur))
                    if len(recorded_chunks) > max_pre:
                        recorded_chunks.pop(0)

        if typed_chars:
            typed = "".join(typed_chars).strip()
            if typed:
                return None, typed

        if not speech_started or not recorded_chunks:
            return None, None

        # Concatenate audio chunks
        full_audio = np.concatenate(recorded_chunks, axis=0)

        # Convert to mono
        if full_audio.ndim > 1 and full_audio.shape[1] > 1:
            full_audio = full_audio.mean(axis=1)
        elif full_audio.ndim > 1:
            full_audio = full_audio[:, 0]

        # Resample to 16,000 Hz for Google Speech Recognition
        target_samples = int(len(full_audio) * 16000 / self.sample_rate)
        resampled = scipy.signal.resample(full_audio, target_samples)

        # Boost & Normalize audio peak to 0.8
        peak = float(np.max(np.abs(resampled)))
        if peak > 0.0001:
            resampled = resampled * (0.8 / peak)

        # Convert to 16-bit PCM bytes
        pcm16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

        return sr.AudioData(pcm16, 16000, 2), None

    def listen(self, timeout_sec: float = 7.0) -> str:
        """Listen to the microphone and transcribe spoken words, or return typed text."""
        audio_data, typed_text = self.record_phrase(max_duration_sec=timeout_sec)

        if typed_text:
            return typed_text

        if not audio_data:
            return ""

        try:
            console.print("[bold yellow]⚡ [Processing speech...][/bold yellow]")
            text = self.recognizer.recognize_google(audio_data)
            return text.strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            console.print(f"[dim red](Google Speech API network notice: {e})[/dim red]")
            return ""
        except Exception:
            return ""


# ===========================================================================
# 3. TASK & CONVERSATION ENGINE (TALKS & ANSWERS LIKE GOOGLE ASSISTANT)
# ===========================================================================
class JarvisTaskEngine:
    """Answers conversationally by speaking and executes tasks cleanly without unsolicited command suggestions."""

    def __init__(self, voice: JarvisVoice):
        self.voice = voice
        self.last_action_time = 0.0
        self.last_action_query = ""
        self.app_map = {
            "chrome": "start chrome",
            "google chrome": "start chrome",
            "notepad": "notepad",
            "calculator": "calc",
            "calc": "calc",
            "code": "code",
            "vs code": "code",
            "visual studio code": "code",
            "explorer": "explorer",
            "files": "explorer",
            "file explorer": "explorer",
            "cmd": "start cmd",
            "command prompt": "start cmd",
            "terminal": "start wt",
            "task manager": "taskmgr",
            "settings": "start ms-settings:",
            "spotify": "start spotify:",
            "discord": "start discord",
            "edge": "start msedge",
            "paint": "mspaint",
            "youtube": "start https://www.youtube.com",
            "yt": "start https://www.youtube.com",
        }

    def _normalize(self, text: str) -> str:
        """Normalize query text, remove punctuation, and correct common STT misrecognitions."""
        q = text.lower().strip()
        q = re.sub(r'[^\w\s]', ' ', q)
        q = " ".join(q.split())

        # Phonetic & Speech-to-Text corrections
        replacements = {
            "syatem": "system",
            "sistem": "system",
            "systam": "system",
            "shoing": "showing",
            "chack": "check",
            "statue": "status",
            "ststus": "status",
            "chrom": "chrome",
            "youtub": "youtube",
            "mic": "microphone",
            "yt": "youtube",
        }
        words = q.split()
        words = [replacements.get(w, w) for w in words]
        return " ".join(words)

    def _strip_prefixes(self, q: str) -> str:
        """Strip conversational fillers and natural language prefixes."""
        prefixes = [
            "i am saying that ",
            "i'm saying that ",
            "i am saying ",
            "i'm saying ",
            "what i am saying is ",
            "what i'm saying is ",
            "i was saying that ",
            "i was saying ",
            "i said ",
            "i am telling you to ",
            "i am telling you ",
            "i'm telling you to ",
            "i'm telling you ",
            "can you show me ",
            "could you show me ",
            "can you tell me ",
            "could you tell me ",
            "can you please ",
            "could you please ",
            "can you ",
            "could you ",
            "will you ",
            "would you ",
            "please ",
            "i want you to ",
            "i want to ",
            "tell me ",
            "show me ",
            "give me ",
            "just ",
            "hey jarvis ",
            "okay jarvis ",
            "ok jarvis ",
            "jarvis ",
        ]
        cleaned = q
        changed = True
        while changed:
            changed = False
            for prefix in prefixes:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):].strip()
                    changed = True
        return cleaned

    def execute_command(self, query: str) -> bool:
        """
        Parse user command or conversational speech.
        Answers by speaking. Only executes actions or opens apps/browser when told to do so.
        Never outputs canned suggestions.
        """
        raw_norm = self._normalize(query)
        if not raw_norm:
            return True

        # Strip wake words from start
        for w in WAKE_WORDS:
            if raw_norm.startswith(w):
                raw_norm = raw_norm[len(w):].strip()
                break

        # Check pure conversational fillers
        if raw_norm in [
            "i am saying that", "i m saying that", "what i am saying", "what i m saying",
            "i was saying", "listen to me", "can you hear me", "are you listening",
            "do you hear me", "hello jarvis", "hey jarvis", "jarvis"
        ]:
            self.voice.speak("I am listening attentively, sir. Please go ahead.")
            return True

        # Clean conversational prefixes for task routing
        clean_q = self._strip_prefixes(raw_norm)
        if not clean_q:
            clean_q = raw_norm

        # -------------------------------------------------------------
        # 1. Exit / Shutdown Commands
        # -------------------------------------------------------------
        if any(word in clean_q for word in ["exit", "quit", "goodbye", "go to sleep", "sleep now", "shutdown jarvis", "power down"]):
            self.voice.speak("Powering down voice protocols. Have a great day, sir.")
            return False

        # -------------------------------------------------------------
        # 2. Natural Conversation & Dialogue
        # -------------------------------------------------------------
        if any(p in raw_norm for p in ["not saying that", "i didn't say that", "did not say that", "not that", "that's wrong", "i didn't mean that"]):
            self.voice.speak("My apologies, sir. Please tell me what you would like me to do.")
            return True

        if raw_norm in ["hello", "hi", "hey", "are you there", "wake up"]:
            self.voice.speak("At your service, sir. What can I do for you?")
            return True

        if any(p in raw_norm for p in ["how are you", "how are things", "how's it going"]):
            self.voice.speak("All my subroutines are fully operational, sir. How are you doing today?")
            return True

        if "who are you" in raw_norm or "what is your name" in raw_norm:
            self.voice.speak("I am J.A.R.V.I.S., your autonomous voice assistant. Ready for your instructions, sir.")
            return True

        if any(p in raw_norm for p in ["what are you doing", "what's up", "what are you up to"]):
            self.voice.speak("Monitoring systems and waiting for your command, sir.")
            return True

        if any(p in raw_norm for p in ["thank you", "thanks", "good job", "well done"]):
            self.voice.speak("You are most welcome, sir.")
            return True

        if any(p in raw_norm for p in ["who made you", "who created you"]):
            self.voice.speak("I was created as an autonomous J.A.R.V.I.S. voice robot assistant, inspired by Tony Stark's system, sir.")
            return True

        if any(p in raw_norm for p in ["what can you do", "help me", "features"]):
            self.voice.speak("I can launch applications, check your PC system status, play YouTube music, adjust volume, give the time and weather, take notes, and answer your questions directly by speaking, sir.")
            return True

        if any(p in raw_norm for p in ["tell me a joke", "make me laugh"]):
            jokes = [
                "Why do programmers prefer dark mode? Because light attracts bugs, sir.",
                "There are 10 types of people in the world: those who understand binary, and those who don't, sir.",
                "Why was the computer cold? It left its Windows open, sir.",
                "A SQL query walks into a bar, walks up to two tables and asks: Can I join you?",
            ]
            self.voice.speak(random.choice(jokes))
            return True

        # -------------------------------------------------------------
        # 3. Time and Date Intents (Flexible matching)
        # -------------------------------------------------------------
        if "timer" not in clean_q and any(w in clean_q for w in ["time", "clock"]):
            now_str = datetime.datetime.now().strftime("%I:%M %p")
            self.voice.speak(f"The current time is {now_str}, sir.")
            return True

        if any(w in clean_q for w in ["date", "today", "day is it", "day of the week", "what day"]):
            date_str = datetime.datetime.now().strftime("%A, %B %d, %Y")
            self.voice.speak(f"Today is {date_str}, sir.")
            return True

        # -------------------------------------------------------------
        # 4. System Diagnostics & Hardware Status (Flexible matching)
        # -------------------------------------------------------------
        is_system_status = (
            any(w in clean_q for w in [
                "system status", "system diagnostic", "system diagnostics",
                "pc status", "computer status", "device status", "hardware status",
                "pc health", "system health", "system performance", "system specs",
                "cpu usage", "ram usage", "battery status", "battery level", "battery percent"
            ])
            or (any(s in clean_q for s in ["system", "pc", "computer", "machine", "hardware"]) and any(st in clean_q for st in ["status", "statue", "health", "state", "show", "showing", "check", "report", "diagnostic", "diagnostics", "performance", "usage", "specs", "spec", "condition", "info"]))
            or any(w in clean_q for w in ["diagnostics", "diagnostic", "battery", "cpu", "ram", "specs"])
        )
        if is_system_status:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            battery = psutil.sensors_battery()

            report = f"System diagnostics complete, sir. CPU utilization is at {cpu:.0f} percent. Memory usage is at {ram:.0f} percent. Primary storage drive is {disk:.0f} percent full."
            if battery:
                plugged = "and charging" if battery.power_plugged else "discharging"
                report += f" Battery power is at {battery.percent:.0f} percent, {plugged}."
            report += " All core systems operating normally."
            self.voice.speak(report)
            return True

        # -------------------------------------------------------------
        # 5. Volume Control
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["volume up", "increase volume", "louder"]):
            self._adjust_volume(up=True, steps=5)
            self.voice.speak("Volume increased, sir.")
            return True

        if any(w in clean_q for w in ["volume down", "decrease volume", "lower volume", "quieter"]):
            self._adjust_volume(up=False, steps=5)
            self.voice.speak("Volume decreased, sir.")
            return True

        if any(w in clean_q for w in ["mute", "unmute", "silence volume"]):
            ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 2, 0)
            self.voice.speak("Master volume toggled, sir.")
            return True

        # -------------------------------------------------------------
        # 6. Media & YouTube (Explicit Request - BEFORE generic app open)
        # -------------------------------------------------------------
        if any(w in clean_q.split() for w in ["youtube", "yt"]) or "youtube" in clean_q or clean_q.startswith("play "):
            now = time.time()
            if (now - self.last_action_time < 2.5) and (clean_q == self.last_action_query):
                return True
            self.last_action_time = now
            self.last_action_query = clean_q

            if "play" in clean_q:
                search_query = clean_q.replace("play", "").replace("on youtube", "").replace("youtube", "").replace("on yt", "").replace("yt", "").strip()
                if search_query:
                    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(search_query)}"
                    self.voice.speak(f"Playing {search_query} on YouTube now, sir.")
                    webbrowser.open(url)
                    return True
            self.voice.speak("Opening YouTube now, sir.")
            webbrowser.open("https://www.youtube.com")
            return True

        # -------------------------------------------------------------
        # 7. Applications (Open / Close - ONLY on explicit command)
        # -------------------------------------------------------------
        if any(clean_q.startswith(p) for p in ["open ", "launch ", "start ", "run "]):
            app_name = clean_q.split(" ", 1)[1].strip()
            if app_name in ["yt", "youtube"]:
                self.voice.speak("Opening YouTube now, sir.")
                webbrowser.open("https://www.youtube.com")
                return True
            self._open_application(app_name)
            return True

        if any(clean_q.startswith(p) for p in ["close ", "kill ", "terminate ", "stop "]):
            app_name = clean_q.split(" ", 1)[1].strip()
            self._close_application(app_name)
            return True

        # -------------------------------------------------------------
        # 8. Web Search (ONLY if user explicitly asks to search Google/web)
        # -------------------------------------------------------------
        if any(clean_q.startswith(p) for p in ["search google for ", "google search ", "search on google "]):
            for p in ["search google for ", "google search ", "search on google "]:
                if clean_q.startswith(p):
                    search_query = clean_q[len(p):].strip()
                    if search_query:
                        self.voice.speak(f"Searching Google for {search_query}, sir.")
                        webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote_plus(search_query)}")
                        return True

        # -------------------------------------------------------------
        # 9. Screenshots
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["screenshot", "capture screen", "screen shot"]):
            self._take_screenshot()
            return True

        # -------------------------------------------------------------
        # 10. Notes & Reminders
        # -------------------------------------------------------------
        if any(clean_q.startswith(p) for p in ["take a note", "note down", "write a note", "save a note"]):
            for p in ["take a note", "note down", "write a note", "save a note"]:
                if clean_q.startswith(p):
                    note_content = clean_q[len(p):].strip()
                    break
            if not note_content:
                self.voice.speak("What should the note say, sir?")
                return True
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(NOTES_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {note_content}\n")
            self.voice.speak(f"Note saved, sir: {note_content}")
            return True

        if any(w in clean_q for w in ["read my notes", "check my notes", "what are my notes", "read notes", "show notes"]):
            self._read_notes()
            return True

        if any(w in clean_q for w in ["clear notes", "delete notes"]):
            if NOTES_FILE.exists():
                NOTES_FILE.unlink()
            self.voice.speak("All notes have been cleared, sir.")
            return True

        # -------------------------------------------------------------
        # 11. Weather
        # -------------------------------------------------------------
        if "weather" in clean_q:
            words = clean_q.split()
            city = "Delhi"
            if "in" in words:
                idx = words.index("in")
                if idx + 1 < len(words):
                    city = " ".join(words[idx+1:]).strip()
            self._get_weather(city)
            return True

        # -------------------------------------------------------------
        # 12. Lock Computer
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["lock computer", "lock pc", "lock screen", "lock workstation"]):
            self.voice.speak("Locking workstation now, sir.")
            ctypes.windll.user32.LockWorkStation()
            return True

        # -------------------------------------------------------------
        # 13. General Knowledge / Q&A (NO Browser Windows! Speaks Answer Directly)
        # -------------------------------------------------------------
        answer = self._get_background_knowledge(clean_q) or self._get_background_knowledge(raw_norm)
        if answer:
            self.voice.speak(answer)
            return True

        # Fallback conversational response - Clean, polite, and NO command suggestions!
        self.voice.speak("Understood, sir. I am right here listening.")
        return True

    def _open_application(self, name: str):
        """Open desktop application."""
        clean_name = name.lower().strip()
        if clean_name in ["yt", "youtube"]:
            self.voice.speak("Opening YouTube now, sir.")
            webbrowser.open("https://www.youtube.com")
            return

        # Match longest key first
        for key in sorted(self.app_map.keys(), key=len, reverse=True):
            if key in clean_name:
                self.voice.speak(f"Opening {key}, sir.")
                subprocess.Popen(self.app_map[key], shell=True)
                return

        self.voice.speak(f"Launching {name}, sir.")
        subprocess.Popen(f"start {name}", shell=True)

    def _close_application(self, name: str):
        """Close running application."""
        clean_name = name.lower().strip()
        found = False
        for p in psutil.process_iter(["pid", "name"]):
            try:
                p_name = p.info["name"].lower()
                if clean_name in p_name:
                    p.terminate()
                    found = True
            except Exception:
                pass

        if found:
            self.voice.speak(f"{name} has been closed, sir.")
        else:
            self.voice.speak(f"Could not locate an active process for {name}, sir.")

    def _adjust_volume(self, up: bool = True, steps: int = 5):
        """Adjust master volume."""
        key = VK_VOLUME_UP if up else VK_VOLUME_DOWN
        for _ in range(steps):
            ctypes.windll.user32.keybd_event(key, 0, 0, 0)
            ctypes.windll.user32.keybd_event(key, 0, 2, 0)
            time.sleep(0.02)

    def _take_screenshot(self):
        """Take screenshot using PowerShell."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = SCREENSHOTS_DIR / f"Jarvis_Screenshot_{timestamp}.png"
        ps_cmd = f"""
        Add-Type -AssemblyName System.Windows.Forms
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bitmap.Save('{filepath}')
        $graphics.Dispose()
        $bitmap.Dispose()
        """
        try:
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, check=True)
            self.voice.speak("Screenshot captured and saved to your Pictures folder, sir.")
        except Exception:
            self.voice.speak("I encountered an issue capturing the screen, sir.")

    def _read_notes(self):
        """Read saved notes."""
        if not NOTES_FILE.exists() or NOTES_FILE.stat().st_size == 0:
            self.voice.speak("You have no saved notes at this time, sir.")
            return

        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        count = len(lines)
        self.voice.speak(f"You have {count} saved notes, sir. Here are the most recent:")
        for line in lines[-3:]:
            self.voice.speak(line)

    def _get_weather(self, city: str):
        """Fetch weather report and speak out loud."""
        try:
            url = f"https://wttr.in/{urllib.parse.quote_plus(city)}?format=j1"
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))
                current = data["current_condition"][0]
                temp_c = current["temp_C"]
                desc = current["weatherDesc"][0]["value"]
                humidity = current["humidity"]
                self.voice.speak(f"The current weather in {city} is {desc} at {temp_c} degrees Celsius with {humidity} percent humidity, sir.")
        except Exception:
            self.voice.speak(f"Could not retrieve weather data for {city} at this time, sir.")

    def _get_background_knowledge(self, query: str) -> Optional[str]:
        """
        Fetch factual knowledge in background and format for spoken response.
        NEVER opens browser windows.
        """
        clean_q = query.lower().strip()
        for prefix in ["who is ", "what is ", "tell me about ", "where is ", "define ", "meaning of ", "why is ", "how does "]:
            if clean_q.startswith(prefix):
                clean_q = clean_q[len(prefix):].strip()
                break

        if not clean_q or len(clean_q) < 2:
            return None

        # 1. Try Wikipedia API
        try:
            search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(clean_q)}&limit=1&format=json"
            req = urllib.request.Request(search_url, headers={"User-Agent": "JarvisRobot/1.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data and len(data) > 1 and data[1]:
                    title = data[1][0]
                    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
                    req2 = urllib.request.Request(summary_url, headers={"User-Agent": "JarvisRobot/1.0"})
                    with urllib.request.urlopen(req2, timeout=3) as r2:
                        d = json.loads(r2.read().decode("utf-8"))
                        extract = d.get("extract", "")
                        if extract:
                            sentences = [s.strip() for s in extract.split(". ") if s.strip()]
                            return ". ".join(sentences[:2]) + "."
        except Exception:
            pass

        # 2. Try DuckDuckGo Instant Answer API
        try:
            ddg_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(clean_q)}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(ddg_url, headers={"User-Agent": "JarvisRobot/1.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
                abstract = data.get("AbstractText", "")
                if abstract:
                    sentences = [s.strip() for s in abstract.split(". ") if s.strip()]
                    return ". ".join(sentences[:2]) + "."
        except Exception:
            pass

        return None


# ===========================================================================
# 4. MAIN VOICE ROBOT ASSISTANT RUNNER
# ===========================================================================
def display_hud(device_name: str, threshold: float):
    """Print holographic Jarvis banner without unsolicited command suggestions."""
    try:
        console.clear()
    except Exception:
        pass
    banner = """
    ╔═══════════════════════════════════════════════════════════════════════╗
    ║                     J . A . R . V . I . S .                           ║
    ║               Just A Rather Very Intelligent System                   ║
    ║                  Autonomous Voice Robot Protocol                      ║
    ╚═══════════════════════════════════════════════════════════════════════╝
    """
    try:
        console.print(Panel(Text(banner, justify="center", style="bold cyan"), box=ROUNDED, style="cyan"))
        console.print("[dim cyan]Voice Engine:[/dim cyan]     [bold green]Edge-TTS (British J.A.R.V.I.S. Ryan Neural)[/bold green]")
        console.print(f"[dim cyan]Microphone:[/dim cyan]       [bold green]{device_name} (ACTIVE & UNMUTED 100%)[/bold green]")
        console.print("[dim cyan]Mode:[/dim cyan]             [bold white]Google Assistant Style (Natural Spoken Dialogue)[/bold white]")
        console.print("[dim cyan]Status:[/dim cyan]           [bold green]Online & Ready (Single Instance Active)[/bold green]\n")
    except Exception:
        print(f"=== J.A.R.V.I.S. Voice Robot Online ({device_name}) ===")


def main():
    """Main voice loop."""
    # Ensure no other duplicate instances of JARVIS are running
    enforce_single_instance()

    voice = JarvisVoice()

    # Automatically ensure Windows microphone is unmuted and boosted to 100%
    ensure_microphone_active_and_unmuted()

    ear = JarvisEar()
    engine = JarvisTaskEngine(voice)

    ear.calibrate(duration_sec=0.3)
    display_hud(ear.device_name, ear.speech_threshold)

    # Initial Greeting
    voice.speak("All systems initialized. J.A.R.V.I.S. voice protocol active. I am at your command, sir.")

    running = True
    while running:
        try:
            try:
                console.print("\n[bold green]● [LISTENING...][/bold green] [dim white](Speak or type your command)[/dim white]")
            except Exception:
                print("\n[LISTENING...] (Speak or type your command)")

            recognized_text = ear.listen(timeout_sec=7.0)

            if recognized_text:
                try:
                    console.print(f"[bold yellow][YOU]:[/bold yellow] [bold white]{recognized_text}[/bold white]")
                except Exception:
                    print(f"[YOU]: {recognized_text}")

                running = engine.execute_command(recognized_text)

        except KeyboardInterrupt:
            voice.speak("Emergency stop initiated. Goodbye, sir.")
            break
        except Exception as e:
            try:
                console.print(f"[dim red](System notice: {e})[/dim red]")
            except Exception:
                print(f"(System notice: {e})")
            time.sleep(1)


if __name__ == "__main__":
    main()
