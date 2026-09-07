# 🤖 J.A.R.V.I.S. — Autonomous Voice Robot Assistant

> **Just A Rather Very Intelligent System** — A voice-first desktop robot assistant inspired by Tony Stark's J.A.R.V.I.S. that speaks out loud and executes real tasks on your computer.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](https://www.microsoft.com/windows)
[![Voice: Edge--TTS](https://img.shields.io/badge/Voice-Edge--TTS%20Neural-cyan.svg)](https://github.com/rany2/edge-tts)
[![Speech: Google STT](https://img.shields.io/badge/Speech--to--Text-Google%20STT-orange.svg)](https://pypi.org/project/SpeechRecognition/)

---

## 🌟 Highlights

- 🗣️ **Pure Voice In / Voice Out**: Listens continuously via microphone and talks back using British J.A.R.V.I.S. neural voice (`en-GB-RyanNeural`).
- ⚡ **100% Free & Fast**: No paid OpenAI/Anthropic API keys required for core voice conversation or task execution.
- 💻 **Real Computer Automation**: Controls apps, searches web/YouTube, controls volume, captures screenshots, and monitors PC health.
- 📊 **Real-Time System Diagnostics**: Reads CPU load, memory utilization, storage space, and battery status.
- 🎨 **Futuristic Terminal HUD**: Holographic console feedback powered by `rich`.
- 🚀 **1-Click Launch**: Double-click `run.bat` to launch instantly.

---

## 🎙️ Spoken Voice Commands

Speak naturally to J.A.R.V.I.S. using any of these commands:

### ⚙️ System & Diagnostics
- *"Jarvis, system status"* or *"Run diagnostics"* → Speaks CPU load, RAM %, Disk %, and Battery % in a full report.
- *"Volume up"* / *"Volume down"* / *"Mute sound"* → Adjusts Windows master volume.
- *"Take a screenshot"* → Captures the screen and saves to `Pictures/Jarvis_Screenshots/`.
- *"Lock computer"* → Immediately locks the Windows workstation.

### 🚀 Application Control
- *"Open Chrome"* / *"Open Google Chrome"*
- *"Open Notepad"*
- *"Open VS Code"* / *"Open Code"*
- *"Open Calculator"*
- *"Open Task Manager"* / *"Open Settings"*
- *"Close Notepad"* / *"Kill Chrome"*

### 🌐 Media & Web
- *"Play Hans Zimmer on YouTube"* / *"Play Interstellar soundtrack"* → Opens YouTube directly to the search/song.
- *"Search Google for artificial intelligence"*
- *"Who is Nikola Tesla"* / *"What is quantum computing"* → Reads aloud a concise spoken summary.
- *"Weather in Delhi"* / *"Weather in London"* → Speaks current temperature, conditions, and humidity.

### 📝 Notes & Utilities
- *"What time is it?"* / *"What is today's date?"*
- *"Take a note buy groceries"* → Saves timestamped note.
- *"Read my notes"* → Speaks recent saved notes.
- *"Clear notes"* → Clears the note file.
- *"Goodbye Jarvis"* / *"Exit"* / *"Sleep now"* → Powers down the voice protocols.

---

## 📦 Installation & Setup

### Prerequisites
- Windows 10 / 11
- Python 3.10+ (Check "Add python.exe to PATH" during installation)
- Microphone & Speakers / Headphones

### 🚀 Quick Start (One-Click)

1. Clone this repository:
   ```bash
   git clone https://github.com/satyamsky801/jarvis-voice-robot.git
   cd jarvis-voice-robot
   ```

2. Double-click **`install.bat`** to create the virtual environment and install dependencies automatically.

3. Double-click **`run.bat`** to launch J.A.R.V.I.S.!

---

## 🛠️ Manual Installation

```bash
# 1. Create virtual environment
python -m venv .venv

# 2. Activate virtual environment
.\.venv\Scripts\activate

# 3. Install requirements
pip install -r requirements.txt

# 4. Start J.A.R.V.I.S.
python voice_robot.py
```

---

## 🏗️ Architecture

```
User Voice ──────────► [Realtek Microphone Array]
                              │
                              ▼
                     [sounddevice 44.1kHz]
                              │
                              ▼
                    [Resampler -> 16kHz Mono]
                              │
                              ▼
                    [Google Speech-to-Text]
                              │
                              ▼
                     [Jarvis Task Engine]
                      ├── App Launching
                      ├── System Diagnostics (psutil)
                      ├── Master Volume (ctypes)
                      ├── Web & YouTube Automation
                      ├── Live Weather & Search
                      └── Screen Capture
                              │
                              ▼
                  [Edge-TTS en-GB-RyanNeural]
                              │
                              ▼
                     [soundfile + sounddevice]
                              │
                              ▼
                      Realtek Speakers
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
