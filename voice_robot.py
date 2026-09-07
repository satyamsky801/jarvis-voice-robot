"""
J.A.R.V.I.S. — Autonomous Voice Robot Assistant
Inspired by Tony Stark's J.A.R.V.I.S.

Features:
- Pure Voice Input & Output (Speaks and listens naturally like a robot)
- Automatic Windows Microphone Health Diagnostic (Detects disabled hardware mic)
- Dynamic Microphone Sensitivity (Auto-calibrating ambient noise)
- Audio Normalization (Boosts quiet laptop microphone levels for Google STT)
- British Ryan Neural Voice via Edge-TTS (100% Free, High Quality)
- Non-blocking Callback Audio Stream (Supports Windows WDM-KS, MME, DirectSound, WASAPI)
- Dual Input: Voice Listening + Instant Keyboard Typing
- Direct Task Execution:
  * Application launching and closing (Chrome, Notepad, Calc, Code, etc.)
  * Web searches & YouTube playback
  * System diagnostics (CPU, RAM, Battery, Disk)
  * Volume control (Up, Down, Mute)
  * Time, Date, Weather
  * Screen capture
  * Quick notes
  * Conversational knowledge
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
# 0. HARDWARE MICROPHONE DIAGNOSTIC
# ===========================================================================
def check_microphone_hardware_status() -> Tuple[bool, str, int]:
    """
    Check if any microphone is active in Windows CoreAudio.
    Returns (is_active, mic_name, state_code)
    """
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetAllDevices()

        # Check for any active capture device
        for d in devices:
            if d.id.startswith("{0.0.1."):
                name = d.FriendlyName or ""
                state = d._dev.GetState()
                if state == 1:  # 1 = ACTIVE
                    return True, name, 1

        # Check if disabled
        for d in devices:
            if d.id.startswith("{0.0.1."):
                name = d.FriendlyName or ""
                state = d._dev.GetState()
                if "Microphone Array" in name or "Microphone" in name:
                    return False, name, state

        return False, "No active microphone found", 2
    except Exception:
        return True, "Unknown", 1


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
        self.speech_threshold = 0.0015
        self.ambient_rms = 0.0005

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

        return 11, "Microphone Array"

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
                self.speech_threshold = max(0.0008, min(0.012, self.ambient_rms * 1.4))
        except Exception:
            self.speech_threshold = 0.0015

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
            # If mic stream failed, fallback to direct console input
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
            console.print("[dim yellow](Audio detected but not understood clearly, try speaking closer or typing)[/dim yellow]")
            return ""
        except sr.RequestError as e:
            console.print(f"[dim red](Google Speech API offline/network error: {e})[/dim red]")
            return ""
        except Exception:
            return ""


# ===========================================================================
# 3. TASK ENGINE (DOING TASKS GIVEN BY USER)
# ===========================================================================
class JarvisTaskEngine:
    """Executes desktop and system tasks requested by the user."""

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
        Parse and execute user command.
        Returns False if user requested to exit, otherwise True.
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
        # 2. Greetings & Status
        # -------------------------------------------------------------
        if q in ["hello", "hi", "hey", "are you there", "wake up"]:
            self.voice.speak("At your service, sir. What task shall I perform?")
            return True

        if "who are you" in q or "what is your name" in q:
            self.voice.speak("I am J.A.R.V.I.S., your autonomous voice robot assistant. Ready for your instructions, sir.")
            return True

        if "how are you" in q:
            self.voice.speak("All my subroutines are fully functional and ready to assist you, sir.")
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
        # 6. Applications (Open / Close)
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
        # 7. Media & YouTube
        # -------------------------------------------------------------
        if "youtube" in q:
            if "play" in q or "search" in q:
                search_query = q.replace("play", "").replace("search", "").replace("on youtube", "").replace("youtube", "").strip()
                if search_query:
                    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(search_query)}"
                    self.voice.speak(f"Playing {search_query} on YouTube now, sir.")
                    webbrowser.open(url)
                    return True
            self.voice.speak("Opening YouTube now, sir.")
            webbrowser.open("https://www.youtube.com")
            return True

        # -------------------------------------------------------------
        # 8. Web Search & Information
        # -------------------------------------------------------------
        if q.startswith("google ") or q.startswith("search for ") or q.startswith("search ") or "look up" in q:
            search_query = q.replace("google ", "").replace("search for ", "").replace("search ", "").replace("look up ", "").strip()
            if search_query:
                self.voice.speak(f"Searching Google for {search_query}, sir.")
                webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote_plus(search_query)}")
                self._quick_search_summary(search_query)
                return True

        if q.startswith("who is ") or q.startswith("what is ") or q.startswith("tell me about "):
            topic = q.replace("who is ", "").replace("what is ", "").replace("tell me about ", "").strip()
            if topic:
                self.voice.speak(f"Accessing information for {topic}, sir.")
                self._quick_search_summary(topic)
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
        # 13. General Fallback Query
        # -------------------------------------------------------------
        self.voice.speak(f"Opening query for {q}, sir.")
        webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}")
        self._quick_search_summary(q)
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
        """Fetch weather report."""
        try:
            url = f"https://wttr.in/{urllib.parse.quote_plus(city)}?format=j1"
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                current = data["current_condition"][0]
                temp_c = current["temp_C"]
                desc = current["weatherDesc"][0]["value"]
                humidity = current["humidity"]
                self.voice.speak(f"The current weather in {city} is {desc} at {temp_c} degrees Celsius with {humidity} percent humidity, sir.")
        except Exception:
            self.voice.speak(f"Could not retrieve weather data for {city} at this time, sir.")

    def _quick_search_summary(self, query: str):
        """Fetch instant summary from DuckDuckGo."""
        try:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(url, headers={"User-Agent": "JarvisRobot/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                abstract = data.get("AbstractText", "")
                if abstract:
                    sentences = abstract.split(". ")
                    summary = ". ".join(sentences[:2]) + "."
                    self.voice.speak(summary)
        except Exception:
            pass


# ===========================================================================
# 4. MAIN VOICE ROBOT ASSISTANT RUNNER
# ===========================================================================
def display_hud(device_name: str, threshold: float, mic_is_active: bool):
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
        
        if mic_is_active:
            console.print(f"[dim cyan]Microphone:[/dim cyan]       [bold green]{device_name} (ACTIVE)[/bold green]")
        else:
            console.print(f"[dim cyan]Microphone:[/dim cyan]       [bold red]{device_name} (DISABLED IN WINDOWS)[/bold red]")

        console.print("[dim cyan]Input Controls:[/dim cyan]   [bold white]Speak into mic OR type command below[/bold white]")
        console.print("[dim cyan]Voice Commands:[/dim cyan]")
        console.print("  * [italic yellow]'open chrome'[/italic yellow], [italic yellow]'open notepad'[/italic yellow], [italic yellow]'open calculator'[/italic yellow], [italic yellow]'open code'[/italic yellow]")
        console.print("  * [italic yellow]'system status'[/italic yellow] or [italic yellow]'diagnostics'[/italic yellow] (CPU, RAM, Battery)")
        console.print("  * [italic yellow]'what time is it'[/italic yellow], [italic yellow]'what is today's date'[/italic yellow], [italic yellow]'weather in Delhi'[/italic yellow]")
        console.print("  * [italic yellow]'play interstellar theme on youtube'[/italic yellow], [italic yellow]'search google for quantum computing'[/italic yellow]")
        console.print("  * [italic yellow]'volume up'[/italic yellow], [italic yellow]'volume down'[/italic yellow], [italic yellow]'mute'[/italic yellow]")
        console.print("  * [italic yellow]'take a screenshot'[/italic yellow]")
        console.print("  * [italic yellow]'take a note buy groceries'[/italic yellow], [italic yellow]'read my notes'[/italic yellow]")
        console.print("  * [italic yellow]'exit'[/italic yellow] or [italic yellow]'goodbye'[/italic yellow] to power down\n")
    except Exception:
        print(f"=== J.A.R.V.I.S. Voice Robot Online ({device_name}) ===")


def main():
    """Main voice loop."""
    voice = JarvisVoice()

    # Diagnostic: Check if Windows has an active microphone
    mic_active, mic_name, mic_state = check_microphone_hardware_status()

    ear = JarvisEar()
    engine = JarvisTaskEngine(voice)

    ear.calibrate(duration_sec=0.3)
    display_hud(ear.device_name, ear.speech_threshold, mic_active)

    if not mic_active:
        console.print(Panel(
            "[bold red]⚠️  ATTENTION: MICROPHONE IS CURRENTLY DISABLED IN WINDOWS![/bold red]\n\n"
            "Windows has disabled your microphone, so it cannot hear your voice yet.\n\n"
            "[bold white]To enable your microphone in 3 clicks:[/bold white]\n"
            "  1. Look at the [bold cyan]Sound Recording[/bold cyan] window that just opened on your screen.\n"
            "  2. In the list, right-click on [bold green]'Microphone Array'[/bold green] (or your mic).\n"
            "  3. Click [bold green]'Enable'[/bold green] and then [bold green]'Set as Default Device'[/bold green]!\n"
            "  4. (If you have an ASUS laptop, also press [bold yellow]F4[/bold yellow] or [bold yellow]Fn + F4[/bold yellow] to unmute the keyboard mic button).\n\n"
            "[italic cyan]Until you enable it, you can also type any command below and press Enter![/italic cyan]",
            title="[bold yellow]Hardware Microphone Alert[/bold yellow]",
            box=ROUNDED,
            style="yellow",
        ))
        # Open the Windows Recording Devices control panel automatically
        try:
            subprocess.Popen("control.exe mmsys.cpl,,1", shell=True)
        except Exception:
            pass

        voice.speak("Notice: Sir, your microphone appears to be disabled in your Windows Sound settings. Please right click Microphone Array in the Sound window and click Enable. In the meantime, you can also type your commands.")
    else:
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
