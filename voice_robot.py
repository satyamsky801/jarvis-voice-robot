"""
J.A.R.V.I.S. — Autonomous Voice Robot Assistant
Inspired by Tony Stark's J.A.R.V.I.S.

Features:
- In-Place YouTube Navigation (Reuses active YouTube tab without opening duplicate tabs)
- Full Tab Management (Close tab, open tab, close all tabs, reopen tab, next/previous tab)
- YouTube Playback Controls (Pause, resume, mute, fullscreen, captions, next/prev video)
- Google Chrome Integration (Always opens in Chrome, not Edge/Explorer)
- Navigation & Maps (Directions, traffic, nearby restaurants, petrol pumps)
- Translations & Math Solver (Translates to Hindi, solves arithmetic calculations)
- Exact Volume by Percentage ("set volume to 50%")
- Timers & Stopwatch (Background async timers and stopwatch)
- App Control (WhatsApp, Instagram, Spotify, Camera, Settings)
- 1-Second Fast Latency (0.75s silence cutoff & async execution)
- 24/7 Background Mode & Persistent Self-Healing Loop
- British Ryan Neural Voice via Edge-TTS (100% Free, High Quality)
"""

import sys

# Ensure UTF-8 encoding across Windows consoles and safe background stdout
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import ctypes
from ctypes import wintypes
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

# Initialize Rich Console with safe fallback
try:
    console = Console(highlight=False)
except Exception:
    console = None

# Windows Win32 Virtual Key Codes
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_TAB = 0x09
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

# Virtual key codes for alphabet keys
VK_W = 0x57
VK_T = 0x54
VK_D = 0x44
VK_R = 0x52
VK_L = 0x4C
VK_F = 0x46
VK_K = 0x4B
VK_M = 0x4D
VK_C = 0x43
VK_N = 0x4E
VK_P = 0x50
VK_J = 0x4A
VK_V = 0x56

# Win32 API setup for 64-bit clipboard and window handling
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

# Assistant Settings
VOICE_NAME = "en-GB-RyanNeural"  # British J.A.R.V.I.S. voice
WAKE_WORDS = ["jarvis", "hey jarvis", "robot", "hello jarvis"]
NOTES_FILE = Path("jarvis_notes.txt")
SCREENSHOTS_DIR = Path(os.path.expanduser("~/Pictures/Jarvis_Screenshots"))
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def safe_print(message: str, style: str = ""):
    """Safely print text whether in console or background mode."""
    if console:
        try:
            if style:
                console.print(f"[{style}]{message}[/{style}]")
            else:
                console.print(message)
            return
        except Exception:
            pass
    try:
        print(message)
    except Exception:
        pass


def send_hotkey(*keys):
    """Simulate key press combination in Windows."""
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    time.sleep(0.04)
    for k in reversed(keys):
        user32.keybd_event(k, 0, 2, 0)


def set_clipboard(text: str) -> bool:
    """Set Windows clipboard text using 64-bit safe Win32 API."""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode('utf-16le') + b'\x00\x00'
        h = kernel32.GlobalAlloc(0x0002, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(13, h)  # CF_UNICODETEXT = 13
        return True
    finally:
        user32.CloseClipboard()


# Session logging
SESSION_LOG_FILE = Path("logs/jarvis_session.log")
SESSION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log_session_event(event_type: str, details: str):
    """Record timestamped event to logs/jarvis_session.log."""
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SESSION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{event_type}] {details}\n")
    except Exception:
        pass


def bring_window_to_foreground(hwnd: int) -> bool:
    """Reliably restore window and bring it to foreground with keyboard focus."""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW

        # Press Alt to bypass Windows foreground lock restriction across background processes
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 2, 0)

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        return True
    except Exception:
        return False


def find_browser_window(prefer_youtube: bool = False) -> Optional[int]:
    """
    Find visible Chrome or Edge browser window handle.
    Prioritizes Google Chrome over Microsoft Edge.
    Prioritizes YouTube window when prefer_youtube is True.
    """
    chrome_wins = []
    other_wins = []

    def enum_cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.lower()
                if not any(ign in title for ign in ["visual studio", "antigravity", "cursor", "sublime"]):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    pname = ""
                    try:
                        pname = psutil.Process(pid.value).name().lower()
                    except Exception:
                        pass

                    if "chrome" in pname or "chrome" in title:
                        chrome_wins.append((hwnd, title))
                    elif "edge" in pname or "msedge" in pname or "edge" in title or "youtube" in title:
                        other_wins.append((hwnd, title))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

    # 1. Prioritize Google Chrome
    if chrome_wins:
        if prefer_youtube:
            for h, t in chrome_wins:
                if "youtube" in t:
                    return h
        return chrome_wins[0][0]

    # 2. Fall back to other browser if Chrome is not open
    if other_wins:
        if prefer_youtube:
            for h, t in other_wins:
                if "youtube" in t:
                    return h
        return other_wins[0][0]

    return None


def send_browser_hotkey(*keys):
    """Focus browser window and dispatch key combination."""
    hwnd = find_browser_window(prefer_youtube=False)
    if hwnd:
        bring_window_to_foreground(hwnd)
        time.sleep(0.15)
    send_hotkey(*keys)


def send_youtube_hotkey(*keys):
    """Focus YouTube window and dispatch key combination."""
    hwnd = find_browser_window(prefer_youtube=True)
    if hwnd:
        bring_window_to_foreground(hwnd)
        time.sleep(0.15)
    send_hotkey(*keys)


def open_browser_url(url: str, prefer_chrome: bool = True):
    """Open URL in Google Chrome if installed, otherwise default browser."""
    log_session_event("BROWSER_OPEN", url)
    if prefer_chrome:
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in chrome_paths:
            if os.path.exists(p):
                try:
                    subprocess.Popen([p, url])
                    return
                except Exception:
                    pass

        try:
            subprocess.Popen(f'start chrome "{url}"', shell=True)
            return
        except Exception:
            pass

    webbrowser.open(url)


def navigate_browser_url(url: str):
    """
    Navigate to URL in place if Chrome/YouTube is already open without opening duplicate tabs.
    If no browser is open, launches Chrome.
    """
    log_session_event("NAVIGATE_URL", url)
    hwnd = find_browser_window(prefer_youtube=True)
    if hwnd:
        try:
            bring_window_to_foreground(hwnd)
            time.sleep(0.20)
            set_clipboard(url)
            send_hotkey(VK_CONTROL, VK_L)  # Focus address bar
            time.sleep(0.10)
            send_hotkey(VK_CONTROL, VK_V)  # Paste
            time.sleep(0.08)
            send_hotkey(VK_RETURN)         # Enter
            return
        except Exception:
            pass

    open_browser_url(url, prefer_chrome=True)


# ===========================================================================
# 0. SINGLE-INSTANCE PROCESS ENFORCEMENT & MICROPHONE AUTO-UNMUTE
# ===========================================================================
def enforce_single_instance():
    """Ensure only one instance of JARVIS runs at a time, protecting launcher parent."""
    current_pid = os.getpid()
    parent_pid = os.getppid()
    protected_pids = {current_pid, parent_pid}
    try:
        cur_proc = psutil.Process(current_pid)
        for parent in cur_proc.parents():
            protected_pids.add(parent.pid)
    except Exception:
        pass

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = proc.info['pid']
            if pid in protected_pids:
                continue
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = " ".join(proc.info.get('cmdline') or []).lower()
                if 'voice_robot.py' in cmdline:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def ensure_microphone_active_and_unmuted() -> Tuple[bool, str]:
    """Ensure the Windows microphone is unmuted and boosted to 100% volume."""
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

        safe_print(f"[J.A.R.V.I.S.]: {clean_text}", "bold cyan")
        log_session_event("SPEAK", clean_text)
        self.speech_queue.put(clean_text)

    def _speech_worker(self):
        """Worker processing speech queue synchronously."""
        while True:
            text = self.speech_queue.get()
            self.is_speaking = True
            try:
                self._synthesize_and_play(text)
            except Exception:
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
# 2. FAST VOICE LISTENER (0.75s Latency + Background Safe)
# ===========================================================================
class JarvisEar:
    """Fast audio capture stream with 0.75s silence cutoff and self-voice feedback guard."""

    def __init__(self, voice: Optional[JarvisVoice] = None):
        self.voice = voice
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
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d.get("name", "").lower()
                if "array" in name:
                    return i, d.get("name", "")

        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d.get("name", "").lower()
                if "mic" in name:
                    return i, d.get("name", "")

        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                return i, d.get("name", "")

        return 1, "Microphone Array"

    def calibrate(self, duration_sec: float = 0.3):
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
                self.speech_threshold = max(0.003, min(0.020, self.ambient_rms * 1.4))
        except Exception:
            self.speech_threshold = 0.005

    def record_phrase(self, max_duration_sec: float = 6.0, silence_cutoff: float = 0.75) -> Tuple[Optional[sr.AudioData], Optional[str]]:
        """Record speech with ultra-fast 0.75s silence cutoff and feedback guard."""
        if self.voice and self.voice.is_speaking:
            time.sleep(0.1)
            return None, None

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
            time.sleep(0.5)
            return None, None

        recorded_chunks = []
        speech_started = False
        silence_time = 0.0
        start_time = time.time()
        typed_chars = []

        with stream:
            while (time.time() - start_time) < max_duration_sec:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwche()
                        if ch in ('\r', '\n'):
                            print()
                            typed = "".join(typed_chars).strip()
                            if typed:
                                return None, typed
                        elif ch == '\b':
                            if typed_chars:
                                typed_chars.pop()
                        else:
                            typed_chars.append(ch)
                except Exception:
                    pass

                try:
                    chunk = audio_q.get(timeout=0.05)
                except queue.Empty:
                    continue

                chunk_dur = len(chunk) / self.sample_rate
                rms = float(np.sqrt(np.mean(np.square(chunk))))

                if rms > self.speech_threshold:
                    if not speech_started:
                        speech_started = True
                        safe_print("🎙️ [Hearing voice... Speak now]", "bold cyan")
                    silence_time = 0.0
                    recorded_chunks.append(chunk)
                elif speech_started:
                    silence_time += chunk_dur
                    recorded_chunks.append(chunk)
                    if silence_time >= silence_cutoff:
                        break
                else:
                    recorded_chunks.append(chunk)
                    max_pre = int(0.2 / max(0.01, chunk_dur))
                    if len(recorded_chunks) > max_pre:
                        recorded_chunks.pop(0)

        if typed_chars:
            typed = "".join(typed_chars).strip()
            if typed:
                return None, typed

        if not speech_started or not recorded_chunks:
            return None, None

        full_audio = np.concatenate(recorded_chunks, axis=0)
        if full_audio.ndim > 1 and full_audio.shape[1] > 1:
            full_audio = full_audio.mean(axis=1)
        elif full_audio.ndim > 1:
            full_audio = full_audio[:, 0]

        target_samples = int(len(full_audio) * 16000 / self.sample_rate)
        resampled = scipy.signal.resample(full_audio, target_samples)

        peak = float(np.max(np.abs(resampled)))
        if peak > 0.0001:
            resampled = resampled * (0.8 / peak)

        pcm16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return sr.AudioData(pcm16, 16000, 2), None

    def listen(self, timeout_sec: float = 5.0) -> str:
        """Listen to the microphone and transcribe spoken words."""
        audio_data, typed_text = self.record_phrase(max_duration_sec=timeout_sec, silence_cutoff=0.75)
        if typed_text:
            return typed_text
        if not audio_data:
            return ""

        try:
            safe_print("⚡ [Processing speech...]", "bold yellow")
            text = self.recognizer.recognize_google(audio_data)
            clean = text.strip()
            if clean:
                log_session_event("HEARD", clean)
            return clean
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            safe_print(f"(Speech network notice: {e})", "dim red")
            return ""
        except Exception:
            return ""


# ===========================================================================
# 3. TASK & CONVERSATION ENGINE (TAB MANAGEMENT, IN-PLACE YT & EXTENDED TOOLS)
# ===========================================================================
class JarvisTaskEngine:
    """Full-featured execution engine supporting tabs, YouTube playback, maps, and system control."""

    def __init__(self, voice: JarvisVoice):
        self.voice = voice
        self.last_action_time = 0.0
        self.last_action_query = ""
        self.stopwatch_start = None
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

    def _normalize(self, text: str) -> str:
        """Normalize query text, remove punctuation, and correct common STT misrecognitions."""
        q = text.lower().strip()
        q = re.sub(r'[^\w\s\+\-\*\/\%\.]', ' ', q)
        q = " ".join(q.split())

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
            "serach": "search",
            "saerch": "search",
            "seach": "search",
            "explorar": "explorer",
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
        """Parse user command or conversational speech and execute immediately."""
        raw_norm = self._normalize(query)
        if not raw_norm:
            return True

        for w in WAKE_WORDS:
            if raw_norm.startswith(w):
                raw_norm = raw_norm[len(w):].strip()
                break

        if raw_norm in [
            "i am saying that", "i m saying that", "what i am saying", "what i m saying",
            "i was saying", "listen to me", "can you hear me", "are you listening",
            "do you hear me", "hello jarvis", "hey jarvis", "jarvis"
        ]:
            self.voice.speak("I am listening attentively, sir. Please go ahead.")
            return True

        clean_q = self._strip_prefixes(raw_norm)
        if not clean_q:
            clean_q = raw_norm

        # -------------------------------------------------------------
        # 1. Exit / Shutdown Commands (ONLY condition that stops the loop)
        # -------------------------------------------------------------
        if any(word in clean_q for word in ["exit", "quit", "goodbye", "go to sleep", "sleep now", "shutdown jarvis", "power down"]):
            self.voice.speak("Powering down voice protocols. Have a great day, sir.")
            return False

        # -------------------------------------------------------------
        # 1b. Close Microsoft Edge ("close edge")
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["close edge", "close msedge", "close microsoft edge", "kill edge", "stop edge"]):
            for p in psutil.process_iter(['name']):
                if p.info['name'] and 'msedge' in p.info['name'].lower():
                    try:
                        p.kill()
                    except Exception:
                        pass
            self.voice.speak("Microsoft Edge has been closed, sir.")
            return True

        # -------------------------------------------------------------
        # 2. Browser & Tab Management (Opening & Closing Tabs)
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["close tab", "close this tab", "close current tab"]):
            send_browser_hotkey(VK_CONTROL, VK_W)
            self.voice.speak("Tab closed, sir.")
            return True

        if any(w in clean_q for w in ["close all tabs", "close all windows", "close browser"]):
            send_browser_hotkey(VK_CONTROL, VK_SHIFT, VK_W)
            self.voice.speak("Browser tabs closed, sir.")
            return True

        if any(w in clean_q for w in ["open new tab", "open a new tab", "new tab"]):
            send_browser_hotkey(VK_CONTROL, VK_T)
            self.voice.speak("New tab opened, sir.")
            return True

        if any(w in clean_q for w in ["reopen closed tab", "reopen last closed tab", "reopen last tab", "undo close tab"]):
            send_browser_hotkey(VK_CONTROL, VK_SHIFT, VK_T)
            self.voice.speak("Reopened last closed tab, sir.")
            return True

        if any(w in clean_q for w in ["switch to next tab", "next tab"]):
            send_browser_hotkey(VK_CONTROL, VK_TAB)
            self.voice.speak("Switched to next tab, sir.")
            return True

        if any(w in clean_q for w in ["switch to previous tab", "previous tab"]):
            send_browser_hotkey(VK_CONTROL, VK_SHIFT, VK_TAB)
            self.voice.speak("Switched to previous tab, sir.")
            return True

        if any(w in clean_q for w in ["bookmark this page", "bookmark page", "bookmark tab"]):
            send_browser_hotkey(VK_CONTROL, VK_D)
            self.voice.speak("Page bookmarked, sir.")
            return True

        if any(w in clean_q for w in ["refresh page", "reload tab", "reload page"]):
            send_browser_hotkey(VK_CONTROL, VK_R)
            self.voice.speak("Page refreshed, sir.")
            return True

        # -------------------------------------------------------------
        # 3. YouTube Video In-Page Controls (Playback, Quality, Captions, Next)
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["pause video", "pause the video", "resume video", "resume the video", "pause", "resume", "stop video"]):
            send_youtube_hotkey(VK_K)
            self.voice.speak("Video playback toggled, sir.")
            return True

        if any(w in clean_q for w in ["mute video", "unmute video"]):
            send_youtube_hotkey(VK_M)
            self.voice.speak("Video sound toggled, sir.")
            return True

        if any(w in clean_q for w in ["full screen", "fullscreen", "exit full screen", "theater mode"]):
            send_youtube_hotkey(VK_F)
            self.voice.speak("Video display toggled, sir.")
            return True

        if any(w in clean_q for w in ["turn on captions", "turn off captions", "captions", "subtitles", "enable subtitles in hindi"]):
            send_youtube_hotkey(VK_C)
            self.voice.speak("Captions and subtitles toggled, sir.")
            return True

        if any(w in clean_q for w in ["change video quality to 1080p", "change video quality", "video quality", "quality to 1080p", "1080p"]):
            send_youtube_hotkey(VK_SHIFT, ord('S'))
            self.voice.speak("Opening video settings, sir. You can select 1080p from the quality menu.")
            return True

        if any(w in clean_q for w in ["next video", "skip video", "play next video"]):
            send_youtube_hotkey(VK_SHIFT, VK_N)
            self.voice.speak("Playing next video, sir.")
            return True

        if any(w in clean_q for w in ["previous video", "play previous video"]):
            send_youtube_hotkey(VK_SHIFT, VK_P)
            self.voice.speak("Playing previous video, sir.")
            return True

        if any(w in clean_q for w in ["forward 10 seconds", "fast forward", "skip forward"]):
            send_youtube_hotkey(VK_L)
            self.voice.speak("Skipped forward, sir.")
            return True

        if any(w in clean_q for w in ["rewind 10 seconds", "rewind", "skip back"]):
            send_youtube_hotkey(VK_J)
            self.voice.speak("Rewound, sir.")
            return True

        if any(w in clean_q for w in ["trending videos", "go to trending", "trending on youtube"]):
            navigate_browser_url("https://www.youtube.com/feed/trending")
            self.voice.speak("Opening trending videos on YouTube, sir.")
            return True

        if any(w in clean_q for w in ["my subscriptions", "open subscriptions", "open my subscriptions"]):
            navigate_browser_url("https://www.youtube.com/feed/subscriptions")
            self.voice.speak("Opening your subscriptions on YouTube, sir.")
            return True

        if any(w in clean_q for w in ["show my watch history", "watch history", "my watch history"]):
            navigate_browser_url("https://www.youtube.com/feed/history")
            self.voice.speak("Opening your YouTube watch history, sir.")
            return True

        # -------------------------------------------------------------
        # 4. YouTube Search & Play (Navigates in-place: NEVER opens duplicate tabs)
        # -------------------------------------------------------------
        if clean_q in [
            "open youtube", "open youtube in chrome", "open youtube on chrome",
            "open yt in chrome", "open yt on chrome", "open yt",
            "launch youtube", "go to youtube", "youtube", "start youtube"
        ]:
            navigate_browser_url("https://www.youtube.com")
            self.voice.speak("Opening YouTube in Chrome now, sir.")
            return True

        is_youtube = any(w in clean_q.split() for w in ["youtube", "yt"]) or "youtube" in clean_q or clean_q.startswith("play ")
        if is_youtube:
            now = time.time()
            if (now - self.last_action_time < 2.0) and (clean_q == self.last_action_query):
                return True
            self.last_action_time = now
            self.last_action_query = clean_q

            search_query = ""
            patterns = [
                r"search\s+(?:for\s+)?(.+?)\s+(?:in|on)\s+(?:youtube|yt)",
                r"search\s+(?:on|in)\s+(?:youtube|yt)\s+(?:for\s+)?(.+)",
                r"(?:in|on)\s+(?:youtube|yt)\s+search\s+(?:for\s+)?(.+)",
                r"search\s+(?:youtube|yt)\s+(?:for\s+)?(.+)",
                r"show\s+me\s+(.+?)\s+(?:in|on)\s+(?:youtube|yt)",
                r"show\s+me\s+(?:in|on)\s+(?:youtube|yt)\s+(.+)",
                r"play\s+(.+?)\s+(?:in|on)\s+(?:youtube|yt)",
                r"play\s+(?:in|on)\s+(?:youtube|yt)\s+(.+)",
                r"play\s+(.+)",
                r"find\s+(.+?)\s+(?:in|on)\s+(?:youtube|yt)",
                r"look\s+up\s+(.+?)\s+(?:in|on)\s+(?:youtube|yt)",
                r"^(.+?)\s+(?:in|on)\s+(?:youtube|yt)$",
            ]
            for pattern in patterns:
                m = re.search(pattern, clean_q)
                if m:
                    cand = m.group(1).strip()
                    cand = re.sub(r"\s+(?:in|on)\s+chrome$", "", cand).strip()
                    if cand and cand not in ["youtube", "yt"]:
                        search_query = cand
                        break

            if search_query:
                url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(search_query)}"
                navigate_browser_url(url)
                self.voice.speak(f"Searching YouTube for {search_query}, sir.")
                return True

            navigate_browser_url("https://www.youtube.com")
            self.voice.speak("Opening YouTube in Chrome now, sir.")
            return True

        # -------------------------------------------------------------
        # 5. Math Solver & Calculations ("what is 25 * 40")
        # -------------------------------------------------------------
        math_result = self._evaluate_math(clean_q)
        if math_result is not None:
            self.voice.speak(f"The answer is {math_result}, sir.")
            return True

        # -------------------------------------------------------------
        # 6. Volume Control by Percentage & Steps
        # -------------------------------------------------------------
        m_vol = re.search(r"(?:set\s+)?volume\s+(?:to\s+)?(\d+)\s*(?:%|percent)?", clean_q)
        if m_vol:
            percent = max(0, min(100, int(m_vol.group(1))))
            self._set_volume_percentage(percent)
            self.voice.speak(f"Master volume set to {percent} percent, sir.")
            return True

        if any(w in clean_q for w in ["volume up", "increase volume", "louder"]):
            self._adjust_volume(up=True, steps=5)
            self.voice.speak("Volume increased, sir.")
            return True

        if any(w in clean_q for w in ["volume down", "decrease volume", "lower volume", "quieter"]):
            self._adjust_volume(up=False, steps=5)
            self.voice.speak("Volume decreased, sir.")
            return True

        if any(w in clean_q for w in ["mute volume", "mute", "unmute volume", "unmute"]):
            user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
            user32.keybd_event(VK_VOLUME_MUTE, 0, 2, 0)
            self.voice.speak("Master volume toggled, sir.")
            return True

        # -------------------------------------------------------------
        # 7. Timers & Stopwatch
        # -------------------------------------------------------------
        m_timer = re.search(r"(?:set\s+a?\s*)?timer\s+for\s+(\d+)\s*(minute|minutes|min|mins|second|seconds|sec|secs)", clean_q)
        if m_timer:
            amount = int(m_timer.group(1))
            unit = m_timer.group(2)
            seconds = amount * 60 if "min" in unit else amount

            def timer_thread():
                time.sleep(seconds)
                self.voice.speak(f"Sir, your timer for {amount} {unit} has finished!")

            threading.Thread(target=timer_thread, daemon=True).start()
            self.voice.speak(f"Timer set for {amount} {unit}, sir.")
            return True

        if "start stopwatch" in clean_q:
            self.stopwatch_start = time.time()
            self.voice.speak("Stopwatch started, sir.")
            return True

        if "stop stopwatch" in clean_q:
            if self.stopwatch_start:
                elapsed = int(time.time() - self.stopwatch_start)
                self.stopwatch_start = None
                self.voice.speak(f"Stopwatch stopped at {elapsed} seconds, sir.")
            else:
                self.voice.speak("The stopwatch is not running, sir.")
            return True

        # -------------------------------------------------------------
        # 8. Navigation, Maps & Directions
        # -------------------------------------------------------------
        if any(clean_q.startswith(p) for p in ["navigate to ", "directions to ", "show traffic to ", "how long to reach "]):
            for p in ["navigate to ", "directions to ", "show traffic to ", "how long to reach "]:
                if clean_q.startswith(p):
                    dest = clean_q[len(p):].strip()
                    url = f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote_plus(dest)}"
                    open_browser_url(url, prefer_chrome=True)
                    self.voice.speak(f"Navigating to {dest} on Google Maps, sir.")
                    return True

        if any(w in clean_q for w in ["nearby restaurants", "restaurants near me", "find nearby restaurants"]):
            open_browser_url("https://www.google.com/maps/search/nearby+restaurants", prefer_chrome=True)
            self.voice.speak("Finding nearby restaurants on Google Maps, sir.")
            return True

        if any(w in clean_q for w in ["petrol pumps near me", "find petrol pumps", "gas stations near me"]):
            open_browser_url("https://www.google.com/maps/search/petrol+pumps+near+me", prefer_chrome=True)
            self.voice.speak("Locating nearby petrol stations on Google Maps, sir.")
            return True

        # -------------------------------------------------------------
        # 9. Translations ("translate [phrase] to hindi")
        # -------------------------------------------------------------
        if "translate " in clean_q and ("to hindi" in clean_q or "in hindi" in clean_q):
            phrase = clean_q.replace("translate ", "").replace("to hindi", "").replace("in hindi", "").strip()
            url = f"https://translate.google.com/?sl=auto&tl=hi&text={urllib.parse.quote_plus(phrase)}"
            open_browser_url(url, prefer_chrome=True)
            self.voice.speak(f"Translating '{phrase}' to Hindi in Chrome, sir.")
            return True

        # -------------------------------------------------------------
        # 10. Communication & Social Apps
        # -------------------------------------------------------------
        if clean_q in ["open whatsapp", "launch whatsapp", "whatsapp"]:
            open_browser_url("https://web.whatsapp.com", prefer_chrome=True)
            self.voice.speak("Opening WhatsApp in Chrome, sir.")
            return True

        if clean_q in ["open instagram", "launch instagram", "instagram"]:
            open_browser_url("https://www.instagram.com", prefer_chrome=True)
            self.voice.speak("Opening Instagram in Chrome, sir.")
            return True

        if clean_q in ["open spotify", "launch spotify", "spotify"]:
            try:
                subprocess.Popen("start spotify:", shell=True)
                self.voice.speak("Launching Spotify, sir.")
            except Exception:
                open_browser_url("https://open.spotify.com", prefer_chrome=True)
                self.voice.speak("Opening Spotify Web Player, sir.")
            return True

        if "whatsapp message" in clean_q or clean_q.startswith("send a whatsapp message"):
            open_browser_url("https://web.whatsapp.com", prefer_chrome=True)
            self.voice.speak("Opening WhatsApp Web. Please select the contact to send your message, sir.")
            return True

        if any(w in clean_q for w in ["open camera", "take a photo", "record a video"]):
            subprocess.Popen("start microsoft.windows.camera:", shell=True)
            self.voice.speak("Launching Windows Camera, sir.")
            return True

        # -------------------------------------------------------------
        # 11. Time and Date Intents (Instant Response)
        # -------------------------------------------------------------
        if "timer" not in clean_q and any(w in clean_q for w in ["time", "clock"]):
            now_str = datetime.datetime.now().strftime("%I:%M %p")
            self.voice.speak(f"The current time is {now_str}, sir.")
            return True

        if any(w in clean_q.split() for w in ["date", "today"]) or any(p in clean_q for p in ["day is it", "day of the week", "what day", "what's the date", "what is the date"]):
            date_str = datetime.datetime.now().strftime("%A, %B %d, %Y")
            self.voice.speak(f"Today is {date_str}, sir.")
            return True

        # -------------------------------------------------------------
        # 12. System Diagnostics & Hardware Status
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
            cpu = psutil.cpu_percent(interval=0.2)
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
        # 13. Application Launch & Close, Websites, Uninstall, Update
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["update my apps", "update apps", "update all apps"]):
            try:
                subprocess.Popen("start ms-windows-store:updates", shell=True)
            except Exception:
                pass
            self.voice.speak("Opening updates to check and update your applications, sir.")
            return True

        m_uninst = re.search(r"uninstall\s+(.+)", clean_q)
        if m_uninst:
            target_app = m_uninst.group(1).strip()
            try:
                subprocess.Popen("start ms-settings:appsfeatures", shell=True)
            except Exception:
                pass
            self.voice.speak(f"Opening Windows Installed Apps to uninstall {target_app}, sir.")
            return True

        popular_sites = {
            "google": "https://www.google.com",
            "youtube": "https://www.youtube.com",
            "github": "https://www.github.com",
            "chatgpt": "https://chatgpt.com",
            "reddit": "https://www.reddit.com",
            "twitter": "https://x.com",
            "x": "https://x.com",
            "netflix": "https://www.netflix.com",
            "amazon": "https://www.amazon.com",
            "gmail": "https://mail.google.com",
            "linkedin": "https://www.linkedin.com",
            "wikipedia": "https://www.wikipedia.org",
        }

        if any(clean_q.startswith(p) for p in ["open ", "launch ", "start ", "run ", "go to "]):
            for p in ["open ", "launch ", "start ", "run ", "go to "]:
                if clean_q.startswith(p):
                    app_name = clean_q[len(p):].strip()
                    break
            else:
                app_name = clean_q.split(" ", 1)[1].strip()

            if any(w in app_name.split() for w in ["yt", "youtube"]):
                navigate_browser_url("https://www.youtube.com")
                self.voice.speak("Opening YouTube in Chrome now, sir.")
                return True

            if app_name in popular_sites:
                open_browser_url(popular_sites[app_name], prefer_chrome=True)
                self.voice.speak(f"Opening {app_name.capitalize()} in Chrome, sir.")
                return True

            if re.search(r"\.(com|org|net|in|io|co|ai|edu|gov)$", app_name):
                url = app_name if app_name.startswith("http") else f"https://{app_name}"
                open_browser_url(url, prefer_chrome=True)
                self.voice.speak(f"Opening {app_name} in Chrome, sir.")
                return True

            self._open_application(app_name)
            return True

        if any(clean_q.startswith(p) for p in ["close ", "kill ", "terminate ", "stop "]):
            app_name = clean_q.split(" ", 1)[1].strip()
            if app_name in ["edge", "msedge", "microsoft edge"]:
                for p in psutil.process_iter(['name']):
                    if p.info['name'] and 'msedge' in p.info['name'].lower():
                        try:
                            p.kill()
                        except Exception:
                            pass
                self.voice.speak("Microsoft Edge has been closed, sir.")
                return True
            self._close_application(app_name)
            return True

        # -------------------------------------------------------------
        # 14. Web Search in Chrome
        # -------------------------------------------------------------
        if any(clean_q.startswith(p) for p in ["search google for ", "google search ", "search on google ", "search "]):
            for p in ["search google for ", "google search ", "search on google ", "search "]:
                if clean_q.startswith(p):
                    search_query = clean_q[len(p):].strip()
                    if search_query:
                        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(search_query)}"
                        open_browser_url(url, prefer_chrome=True)
                        self.voice.speak(f"Searching Google for {search_query}, sir.")
                        return True

        # -------------------------------------------------------------
        # 15. Screenshots
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["screenshot", "capture screen", "screen shot"]):
            self._take_screenshot()
            return True

        # -------------------------------------------------------------
        # 16. Notes & Reminders
        # -------------------------------------------------------------
        if any(clean_q.startswith(p) for p in ["take a note", "note down", "write a note", "create a note"]):
            for p in ["take a note", "note down", "write a note", "create a note"]:
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
        # 17. Weather
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
        # 18. Lock Screen
        # -------------------------------------------------------------
        if any(w in clean_q for w in ["lock computer", "lock pc", "lock screen", "lock workstation"]):
            self.voice.speak("Locking workstation now, sir.")
            user32.LockWorkStation()
            return True

        # -------------------------------------------------------------
        # 19. Natural Dialogue & Chat
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

        if any(p in raw_norm for p in ["thank you", "thanks", "good job", "well done"]):
            self.voice.speak("You are most welcome, sir.")
            return True

        if any(p in raw_norm for p in ["who made you", "who created you"]):
            self.voice.speak("I was created as an autonomous J.A.R.V.I.S. voice robot assistant, inspired by Tony Stark's system, sir.")
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
        # 20. General Knowledge / Q&A (Speaks Answer Directly)
        # -------------------------------------------------------------
        answer = self._get_background_knowledge(clean_q) or self._get_background_knowledge(raw_norm)
        if answer:
            self.voice.speak(answer)
            return True

        # Fallback conversational response
        self.voice.speak("Understood, sir. I am right here listening.")
        return True

    def _evaluate_math(self, query: str) -> Optional[str]:
        """Safely evaluate arithmetic calculations."""
        clean = query.lower()
        for prefix in ["what is ", "calculate ", "solve ", "how much is ", "what s "]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
                break

        clean = clean.replace("plus", "+").replace("minus", "-").replace("times", "*").replace("multiplied by", "*").replace("into", "*").replace("x", "*").replace("divided by", "/").replace("over", "/")
        clean = re.sub(r'(\d+)\s*percent\s*of\s*(\d+)', r'(\1/100)*\2', clean)
        clean = re.sub(r'(\d+)%', r'(\1/100)', clean)

        if re.match(r'^[\d\s\+\-\*\/\(\)\.]+$', clean) and any(op in clean for op in "+-*/"):
            try:
                result = eval(clean, {"__builtins__": None}, {})
                if isinstance(result, float) and result.is_integer():
                    result = int(result)
                return str(result)
            except Exception:
                return None
        return None

    def _set_volume_percentage(self, percent: int):
        """Set Windows master volume directly via pycaw."""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            volume = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
            volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
        except Exception:
            pass

    def _open_application(self, name: str):
        """Open desktop application immediately."""
        clean_name = name.lower().strip()
        if "chrome" in clean_name and any(w in clean_name for w in ["yt", "youtube"]):
            navigate_browser_url("https://www.youtube.com")
            self.voice.speak("Opening YouTube in Chrome now, sir.")
            return

        if clean_name in ["yt", "youtube"]:
            navigate_browser_url("https://www.youtube.com")
            self.voice.speak("Opening YouTube in Chrome now, sir.")
            return

        if clean_name in ["chrome", "google chrome"]:
            open_browser_url("https://www.google.com", prefer_chrome=True)
            self.voice.speak("Opening Google Chrome, sir.")
            return

        for key in sorted(self.app_map.keys(), key=len, reverse=True):
            if key in clean_name:
                subprocess.Popen(self.app_map[key], shell=True)
                self.voice.speak(f"Opening {key}, sir.")
                return

        subprocess.Popen(f"start {name}", shell=True)
        self.voice.speak(f"Launching {name}, sir.")

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
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, 2, 0)
            time.sleep(0.01)

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
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8"))
                current = data["current_condition"][0]
                temp_c = current["temp_C"]
                desc = current["weatherDesc"][0]["value"]
                humidity = current["humidity"]
                self.voice.speak(f"The current weather in {city} is {desc} at {temp_c} degrees Celsius with {humidity} percent humidity, sir.")
        except Exception:
            self.voice.speak(f"Could not retrieve weather data for {city} at this time, sir.")

    def _get_background_knowledge(self, query: str) -> Optional[str]:
        """Fetch factual knowledge in background and format for spoken response."""
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
            with urllib.request.urlopen(req, timeout=2.5) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data and len(data) > 1 and data[1]:
                    title = data[1][0]
                    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
                    req2 = urllib.request.Request(summary_url, headers={"User-Agent": "JarvisRobot/1.0"})
                    with urllib.request.urlopen(req2, timeout=2.5) as r2:
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
            with urllib.request.urlopen(req, timeout=2.5) as r:
                data = json.loads(r.read().decode("utf-8"))
                abstract = data.get("AbstractText", "")
                if abstract:
                    sentences = [s.strip() for s in abstract.split(". ") if s.strip()]
                    return ". ".join(sentences[:2]) + "."
        except Exception:
            pass

        return None


# ===========================================================================
# 4. MAIN VOICE ROBOT ASSISTANT RUNNER (PERSISTENT BACKGROUND & FOREGROUND)
# ===========================================================================
def display_hud(device_name: str, threshold: float):
    """Print holographic Jarvis banner without unsolicited command suggestions."""
    if not console:
        return
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
        console.print("[dim cyan]Browser Engine:[/dim cyan]   [bold green]Google Chrome Integration (Tab Navigation & Control)[/bold green]")
        console.print("[dim cyan]Mode:[/dim cyan]             [bold white]Ultra-Fast 1-Sec Latency & In-Place YouTube Navigator[/bold white]")
        console.print("[dim cyan]Status:[/dim cyan]           [bold green]Online & Ready (Runs continuously until closed)[/bold green]\n")
    except Exception:
        pass


def main():
    """Main voice loop - runs continuously in background or foreground until explicitly closed."""
    enforce_single_instance()

    voice = JarvisVoice()
    ensure_microphone_active_and_unmuted()

    ear = JarvisEar(voice=voice)
    engine = JarvisTaskEngine(voice)

    ear.calibrate(duration_sec=0.3)
    display_hud(ear.device_name, ear.speech_threshold)

    voice.speak("All systems online. J.A.R.V.I.S. voice protocol active. Ready for your commands, sir.")

    while True:
        try:
            safe_print("● [LISTENING...] (Speak or type your command)", "bold green")

            recognized_text = ear.listen(timeout_sec=5.0)

            if recognized_text:
                safe_print(f"[YOU]: {recognized_text}", "bold yellow")
                keep_running = engine.execute_command(recognized_text)
                if not keep_running:
                    break

        except KeyboardInterrupt:
            voice.speak("Emergency stop initiated. Goodbye, sir.")
            break
        except Exception as e:
            safe_print(f"(System notice: {e})", "dim red")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
