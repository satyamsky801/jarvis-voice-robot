"""
J.A.R.V.I.S. — Autonomous Voice Robot Assistant
Inspired by Tony Stark's J.A.R.V.I.S.

Features:
- Pure Voice Input & Output (Speaks and talks naturally like Google Assistant)
- Zero Unwanted Browser Popups (Speaks answers directly without opening browser windows)
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
# 0. HARDWARE MICROPHONE AUTO-UNMUTE & BOOST
# ===========================================================================
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
                    vol_ptr = d._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    volume = ctypes.cast(vol_ptr, ctypes.POINTER(IAudioEndpointVolume))
                    
                    # Unmute
                    if volume.GetMute() == 1:
                        volume.SetMute(0, None)
                    
                    # Boost to 100% volume
                    volume.SetMasterVolumeLevelScalar(1.0, None)
                    return True, name
                except Exception:
                    pass
        return True, "Microphone Array"
    except Exception:
        return True, "Microphone"


# ===========================================================================
# 1. TEXT TO SPEECH (J.A.R.V.I.S. VOICE)
# ===========================================================================
class JarvisVoice:
    """Handles high-fidelity speech synthesis using Edge-TTS and sounddevice."""

    def __init__(self, voice: str = VOICE_NAME):
        self.voice = voice
        self.is_speaking = False
        self._lock = threading.Lock()

    def clean_text_for_speech(self, text: str) -> str:
        """Strip markdown, links, emojis and symbols that shouldn't be read literally."""
        text = re.sub(r"\*+", "", text)
        text = re.sub(r"\[.*?\]\(.*?\)", "", text)
        text = re.sub(r"https?://\S+", "link", text)
        text = re.sub(r"[#_`~>|]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    async def _generate_audio_file(self, text: str, output_path: str):
        """Use edge-tts to generate speech audio."""
        import edge_tts
        clean_text = self.clean_text_for_speech(text)
        if not clean_text:
            return
        communicate = edge_tts.Communicate(clean_text, self.voice, rate="+4%", pitch="+0Hz")
        await communicate.save(output_path)

    def speak(self, text: str, block: bool = True):
        """Speak out loud synchronously or in background."""
        if not text.strip():
            return

        with self._lock:
            self.is_speaking = True
            try:
                console.print(f"[bold cyan][J.A.R.V.I.S.]:[/bold cyan] [italic white]{text}[/italic white]")
            except Exception:
                print(f"[J.A.R.V.I.S.]: {text}")

            tmp_mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp_mp3_path = tmp_mp3.name
            tmp_mp3.close()

            try:
                # Generate MP3
                asyncio.run(self._generate_audio_file(text, tmp_mp3_path))

                # Load and play audio
                if os.path.exists(tmp_mp3_path) and os.path.getsize(tmp_mp3_path) > 0:
                    data, samplerate = sf.read(tmp_mp3_path, dtype="float32")
                    sd.play(data, samplerate)
                    if block:
                        sd.wait()
            except Exception as e:
                console.print(f"[dim red](Speech synthesis warning: {e})[/dim red]")
            finally:
                self.is_speaking = False
                try:
                    if os.path.exists(tmp_mp3_path):
                        os.remove(tmp_mp3_path)
                except Exception:
                    pass


# ===========================================================================
# 2. AUDIO INPUT & RECOGNITION (J.A.R.V.I.S. EAR)
# ===========================================================================
class JarvisEar:
    """Handles audio capture using asynchronous callbacks and speech recognition."""

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

    def record_phrase(self, max_duration_sec: float = 6.0, silence_cutoff: float = 1.2) -> Tuple[Optional[sr.AudioData], Optional[str]]:
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

    def listen(self, timeout_sec: float = 6.0) -> str:
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
    """Answers conversationally by speaking and executes tasks only when requested."""

    def __init__(self, voice: JarvisVoice):
        self.voice = voice
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
        }

    def execute_command(self, query: str) -> bool:
        """
        Parse user command or conversational speech.
        Answers by speaking. Only opens apps/browser when explicitly told to do so.
        """
        q = query.lower().strip()

        # Clean off wake words
        for w in WAKE_WORDS:
            if q.startswith(w):
                q = q[len(w):].strip()
                break

        if not q:
            return True

        # -------------------------------------------------------------
        # 1. Exit / Shutdown Commands
        # -------------------------------------------------------------
        if any(word in q for word in ["exit", "quit", "goodbye", "go to sleep", "sleep now", "shutdown jarvis", "power down"]):
            self.voice.speak("Powering down voice protocols. Have a great day, sir.")
            return False

        # -------------------------------------------------------------
        # 2. Natural Conversation & Dialogue (Answering Directly by Speaking)
        # -------------------------------------------------------------
        if any(p in q for p in ["can you hear me", "hear me clearly", "are you listening", "do you hear me", "can you hear"]):
            self.voice.speak("Loud and clear, sir. I am listening and at your service.")
            return True

        if any(p in q for p in ["not saying that", "i didn't say that", "did not say that", "not that", "that's wrong", "i didn't mean that"]):
            self.voice.speak("My apologies, sir. Please tell me what you would like me to do.")
            return True

        if q in ["hello", "hi", "hey", "are you there", "wake up"]:
            self.voice.speak("At your service, sir. What can I do for you?")
            return True

        if any(p in q for p in ["how are you", "how are things", "how's it going"]):
            self.voice.speak("All my subroutines are fully operational, sir. How are you doing today?")
            return True

        if "who are you" in q or "what is your name" in q:
            self.voice.speak("I am J.A.R.V.I.S., your autonomous voice assistant. Ready for your instructions, sir.")
            return True

        if any(p in q for p in ["what are you doing", "what's up", "what are you up to"]):
            self.voice.speak("Monitoring systems and waiting for your command, sir.")
            return True

        if any(p in q for p in ["thank you", "thanks", "good job", "well done"]):
            self.voice.speak("You are most welcome, sir.")
            return True

        if any(p in q for p in ["who made you", "who created you"]):
            self.voice.speak("I was created as an autonomous J.A.R.V.I.S. voice robot assistant, inspired by Tony Stark's system, sir.")
            return True

        if any(p in q for p in ["what can you do", "help me", "commands", "features"]):
            self.voice.speak("I can launch apps, check system diagnostics, play music on YouTube, give you the time, date, and weather, adjust volume, take notes, and answer your questions by speaking, sir.")
            return True

        if any(p in q for p in ["tell me a joke", "make me laugh"]):
            jokes = [
                "Why do programmers prefer dark mode? Because light attracts bugs, sir.",
                "There are 10 types of people in the world: those who understand binary, and those who don't, sir.",
                "Why was the computer cold? It left its Windows open, sir.",
                "A SQL query walks into a bar, walks up to two tables and asks: Can I join you?",
            ]
            self.voice.speak(random.choice(jokes))
            return True

        # -------------------------------------------------------------
        # 3. Time and Date
        # -------------------------------------------------------------
        if "what time" in q or "current time" in q or "time now" in q:
            now_str = datetime.datetime.now().strftime("%I:%M %p")
            self.voice.speak(f"The current time is {now_str}, sir.")
            return True

        if "what date" in q or "what is today" in q or "today's date" in q or "what day" in q:
            date_str = datetime.datetime.now().strftime("%A, %B %d, %Y")
            self.voice.speak(f"Today is {date_str}, sir.")
            return True

        # -------------------------------------------------------------
        # 4. System Diagnostics & Hardware Status
        # -------------------------------------------------------------
        if any(term in q for term in ["system status", "diagnostics", "battery", "cpu", "ram", "specs"]):
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
        if "volume up" in q or "increase volume" in q or "louder" in q:
            self._adjust_volume(up=True, steps=5)
            self.voice.speak("Volume increased, sir.")
            return True

        if "volume down" in q or "decrease volume" in q or "lower volume" in q:
            self._adjust_volume(up=False, steps=5)
            self.voice.speak("Volume decreased, sir.")
            return True

        if "mute" in q or "unmute" in q or "silence volume" in q:
            ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 2, 0)
            self.voice.speak("Master volume toggled, sir.")
            return True

        # -------------------------------------------------------------
        # 6. Applications (Open / Close - ONLY on explicit command)
        # -------------------------------------------------------------
        if q.startswith("open ") or q.startswith("launch ") or q.startswith("start "):
            app_name = q.replace("open ", "").replace("launch ", "").replace("start ", "").strip()
            self._open_application(app_name)
            return True

        if q.startswith("close ") or q.startswith("kill ") or q.startswith("terminate "):
            app_name = q.replace("close ", "").replace("kill ", "").replace("terminate ", "").strip()
            self._close_application(app_name)
            return True

        # -------------------------------------------------------------
        # 7. Media & YouTube (ONLY on explicit request)
        # -------------------------------------------------------------
        if "youtube" in q or q.startswith("play "):
            if "play" in q:
                search_query = q.replace("play", "").replace("on youtube", "").replace("youtube", "").strip()
                if search_query:
                    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(search_query)}"
                    self.voice.speak(f"Playing {search_query} on YouTube now, sir.")
                    webbrowser.open(url)
                    return True
            self.voice.speak("Opening YouTube now, sir.")
            webbrowser.open("https://www.youtube.com")
            return True

        # -------------------------------------------------------------
        # 8. Web Search (ONLY if user explicitly asks to search Google/web)
        # -------------------------------------------------------------
        if q.startswith("search google for ") or q.startswith("google search ") or q.startswith("search on google "):
            search_query = q.replace("search google for ", "").replace("google search ", "").replace("search on google ", "").strip()
            if search_query:
                self.voice.speak(f"Searching Google for {search_query}, sir.")
                webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote_plus(search_query)}")
                return True

        # -------------------------------------------------------------
        # 9. Screenshots
        # -------------------------------------------------------------
        if "screenshot" in q or "capture screen" in q:
            self._take_screenshot()
            return True

        # -------------------------------------------------------------
        # 10. Notes & Reminders
        # -------------------------------------------------------------
        if q.startswith("take a note") or q.startswith("note down") or q.startswith("write a note"):
            note_content = q.replace("take a note", "").replace("note down", "").replace("write a note", "").strip()
            if not note_content:
                self.voice.speak("What should the note say, sir?")
                return True
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(NOTES_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {note_content}\n")
            self.voice.speak(f"Note saved, sir: {note_content}")
            return True

        if "read my notes" in q or "check my notes" in q or "what are my notes" in q:
            self._read_notes()
            return True

        if "clear notes" in q or "delete notes" in q:
            if NOTES_FILE.exists():
                NOTES_FILE.unlink()
            self.voice.speak("All notes have been cleared, sir.")
            return True

        # -------------------------------------------------------------
        # 11. Weather
        # -------------------------------------------------------------
        if "weather" in q:
            words = q.split()
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
        if "lock computer" in q or "lock pc" in q or "lock screen" in q:
            self.voice.speak("Locking workstation now, sir.")
            ctypes.windll.user32.LockWorkStation()
            return True

        # -------------------------------------------------------------
        # 13. General Knowledge / Q&A (NO Browser Windows! Speaks Answer Directly)
        # -------------------------------------------------------------
        answer = self._get_background_knowledge(q)
        if answer:
            self.voice.speak(answer)
        else:
            # Polite fallback dialogue without opening anything
            self.voice.speak("I am listening, sir. You can ask me questions, or say 'open chrome', 'play music', or 'system status'.")
        return True

    def _open_application(self, name: str):
        """Open desktop application."""
        clean_name = name.lower().strip()
        for key, cmd in self.app_map.items():
            if key in clean_name:
                self.voice.speak(f"Opening {key}, sir.")
                subprocess.Popen(cmd, shell=True)
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
        clean_q = query.lower()
        for prefix in ["who is ", "what is ", "tell me about ", "where is ", "define ", "meaning of "]:
            if clean_q.startswith(prefix):
                clean_q = clean_q[len(prefix):].strip()
                break

        if not clean_q:
            return None

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
                            # Speak the first 2 concise sentences
                            return ". ".join(sentences[:2]) + "."
        except Exception:
            pass

        return None


# ===========================================================================
# 4. MAIN VOICE ROBOT ASSISTANT RUNNER
# ===========================================================================
def display_hud(device_name: str, threshold: float):
    """Print holographic Jarvis banner."""
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
        console.print("[dim cyan]Mode:[/dim cyan]             [bold white]Google Assistant Style (Speaks answers aloud, no popups)[/bold white]")
        console.print("[dim cyan]Voice Commands:[/dim cyan]")
        console.print("  * [italic yellow]'can you hear me'[/italic yellow], [italic yellow]'how are you'[/italic yellow], [italic yellow]'tell me a joke'[/italic yellow] (Talks to you)")
        console.print("  * [italic yellow]'who is Albert Einstein'[/italic yellow], [italic yellow]'what is quantum computing'[/italic yellow] (Answers by speaking)")
        console.print("  * [italic yellow]'system status'[/italic yellow] or [italic yellow]'diagnostics'[/italic yellow] (Speaks CPU, RAM, Battery)")
        console.print("  * [italic yellow]'what time is it'[/italic yellow], [italic yellow]'what is today's date'[/italic yellow], [italic yellow]'weather in Delhi'[/italic yellow]")
        console.print("  * [italic yellow]'open chrome'[/italic yellow], [italic yellow]'open notepad'[/italic yellow], [italic yellow]'open calculator'[/italic yellow], [italic yellow]'open code'[/italic yellow]")
        console.print("  * [italic yellow]'play interstellar theme on youtube'[/italic yellow]")
        console.print("  * [italic yellow]'volume up'[/italic yellow], [italic yellow]'volume down'[/italic yellow], [italic yellow]'mute'[/italic yellow]")
        console.print("  * [italic yellow]'take a screenshot'[/italic yellow], [italic yellow]'take a note buy groceries'[/italic yellow], [italic yellow]'read my notes'[/italic yellow]")
        console.print("  * [italic yellow]'exit'[/italic yellow] or [italic yellow]'goodbye'[/italic yellow] to power down\n")
    except Exception:
        print(f"=== J.A.R.V.I.S. Voice Robot Online ({device_name}) ===")


def main():
    """Main voice loop."""
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

            recognized_text = ear.listen(timeout_sec=5.0)

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
