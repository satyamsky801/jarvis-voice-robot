"""
JARVIS Desktop Mascot Companion
An interactive, animated 3D robot character that floats on your Windows desktop,
reacts in real-time to voice commands, LLM thinking, speech, and PC task execution.

Includes:
- Lifelike zero-g floating physics, horizontal swaying, and dynamic rotational tilting
- 125 BPM animated dancing routine with alternating steps & floating musical notes
- Thumbs-up approval gesture and golden sparkles
- Remorseful kneeling-down apology pose when JARVIS cannot answer a question
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import random
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont, ImageTk

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(r"C:\assist\jarvis\assets")
SETTINGS_FILE = Path(r"C:\assist\jarvis\mascot_settings.json")
TRANSPARENT_COLOR = "#010101"

# Preset dimensions for mascot
SIZES = {
    "small": (130, 210),
    "medium": (175, 284),
    "large": (230, 373),
}


class JarvisDesktopMascot:
    """
    Frameless, transparent, draggable desktop mascot companion.
    Visually represents JARVIS on screen with real-time reactive animations.
    """

    def __init__(
        self,
        on_trigger_listen: Optional[Callable[[], None]] = None,
        on_typed_command: Optional[Callable[[str], None]] = None,
        on_toggle_mute: Optional[Callable[[], bool]] = None,
    ):
        self.on_trigger_listen = on_trigger_listen
        self.on_typed_command = on_typed_command
        self.on_toggle_mute = on_toggle_mute

        self.root = tk.Tk()
        self.root.title("JARVIS Mascot")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.config(bg=TRANSPARENT_COLOR)

        # Mascot state: idle, blink, listen, think, speak, action, thumbsup, kneedown, dance
        self.state = "idle"
        self.current_size = "medium"
        self.bubble_text = ""
        self.bubble_title = ""
        self.bubble_expire_time = 0.0
        self.is_muted = False
        self.is_running = True

        # Mouse dragging state
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.has_moved = False

        # Animation timing & state timeouts
        self.anim_frame = 0
        self.last_blink_time = time.time()
        self.blink_duration = 0.2
        self.is_blinking = False
        self.action_reset_time = 0.0
        self.thumbsup_reset_time = 0.0
        self.kneedown_reset_time = 0.0
        self.dance_reset_time = 0.0
        self.speech_talk_cycle = 0

        # Floating musical note particles for dancing state
        self.particles = []

        # Communication queue
        self.event_queue = queue.Queue()

        # Canvas sizing & position
        self._load_settings()
        char_w, char_h = SIZES[self.current_size]
        self.canvas_w = max(char_w + 140, 320)
        self.canvas_h = char_h + 130
        self.char_x = self.canvas_w // 2
        self.char_y = 100 + char_h // 2

        self._setup_window_position()
        self._setup_canvas()
        self._load_images()
        self._bind_events()

        # Start animation and event loops
        self.root.after(30, self._animation_loop)
        self.root.after(25, self._poll_queue)

    def _load_settings(self):
        """Load saved size and screen position."""
        self.saved_x = None
        self.saved_y = None
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.current_size = data.get("size", "medium")
                    self.saved_x = data.get("x")
                    self.saved_y = data.get("y")
            except Exception:
                pass

    def _save_settings(self):
        """Save current position and size."""
        try:
            data = {
                "size": self.current_size,
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y(),
            }
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _setup_window_position(self):
        """Position window on desktop (default bottom-right)."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        if self.saved_x is not None and self.saved_y is not None:
            x = max(0, min(screen_w - self.canvas_w, self.saved_x))
            y = max(0, min(screen_h - self.canvas_h, self.saved_y))
        else:
            x = screen_w - self.canvas_w - 30
            y = screen_h - self.canvas_h - 70

        self.root.geometry(f"{self.canvas_w}x{self.canvas_h}+{x}+{y}")

    def _setup_canvas(self):
        """Create transparent canvas."""
        self.canvas = tk.Canvas(
            self.root,
            width=self.canvas_w,
            height=self.canvas_h,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

    def _load_images(self):
        """Pre-cache resized images and rotation buffers for fast rendering."""
        target_size = SIZES[self.current_size]
        self.raw_images = {}
        self.rotated_cache = {}

        all_states = [
            "idle", "blink", "listen", "think", "speak",
            "action", "thumbsup", "kneedown", "dance_1", "dance_2"
        ]

        for state_name in all_states:
            img_path = ASSETS_DIR / f"mascot_{state_name}.png"
            if not img_path.exists():
                img_path = ASSETS_DIR / "mascot_idle.png"

            try:
                raw_img = Image.open(img_path).convert("RGBA")
                resized = raw_img.resize(target_size, Image.Resampling.LANCZOS)
                self.raw_images[state_name] = resized
            except Exception as e:
                logger.error("Failed to load mascot image %s: %s", state_name, e)

    def _get_rendered_image(self, state_name: str, tilt_angle: float) -> Optional[ImageTk.PhotoImage]:
        """Get or compute rotated PhotoImage from memory cache."""
        # Quantize angle to nearest 0.5 degrees for buttery performance
        q_angle = round(tilt_angle * 2.0) / 2.0
        key = (state_name, q_angle)
        if key in self.rotated_cache:
            return self.rotated_cache[key]

        raw = self.raw_images.get(state_name) or self.raw_images.get("idle")
        if not raw:
            return None

        try:
            if abs(q_angle) < 0.2:
                rotated = raw
            else:
                rotated = raw.rotate(-q_angle, resample=Image.Resampling.BICUBIC, expand=False)

            bg = Image.new("RGBA", rotated.size, (1, 1, 1, 255))
            comp = Image.alpha_composite(bg, rotated)
            photo = ImageTk.PhotoImage(comp)

            # Keep cache bounded to save RAM
            if len(self.rotated_cache) > 100:
                self.rotated_cache.clear()

            self.rotated_cache[key] = photo
            return photo
        except Exception as e:
            logger.error("Rotation error: %s", e)
            return None

    def _bind_events(self):
        """Bind mouse drag, clicks, and context menu."""
        self.canvas.bind("<Button-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Button-3>", self._on_right_click)

    def _on_mouse_down(self, event):
        """Start dragging."""
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.has_moved = False

    def _on_mouse_drag(self, event):
        """Update window position during drag."""
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self.has_moved = True
            new_x = self.root.winfo_x() + dx
            new_y = self.root.winfo_y() + dy
            self.root.geometry(f"+{new_x}+{new_y}")

    def _on_mouse_up(self, event):
        """Detect click vs drag release."""
        if self.has_moved:
            self._save_settings()
        else:
            # Click on character: trigger voice listening
            self.show_speech("Listening...", title="MIC ACTIVE", duration=3.0)
            self.set_state("listen")
            if self.on_trigger_listen:
                threading.Thread(target=self.on_trigger_listen, daemon=True).start()

    def _on_right_click(self, event):
        """Show context menu."""
        menu = tk.Menu(self.root, tearoff=0, bg="#1a1d24", fg="#ffffff", activebackground="#00d4ff", activeforeground="#000000", font=("Segoe UI", 10))
        menu.add_command(label="🎙️ Listen Now", command=self._trigger_listen_menu)
        menu.add_command(label="💬 Type Command...", command=self._open_type_dialog)
        menu.add_separator()

        # Fun animations sub-actions
        menu.add_command(label="💃 Dance!", command=lambda: self.dance(6.0))
        menu.add_command(label="👍 Thumbs Up", command=lambda: self.thumbs_up(3.0))
        menu.add_command(label="🙇 Kneel Down (Sorry)", command=lambda: self.kneel_down(4.0))
        menu.add_separator()

        size_menu = tk.Menu(menu, tearoff=0, bg="#1a1d24", fg="#ffffff", activebackground="#00d4ff", activeforeground="#000000")
        size_menu.add_command(label="Small (130px)", command=lambda: self.change_size("small"))
        size_menu.add_command(label="Medium (175px)", command=lambda: self.change_size("medium"))
        size_menu.add_command(label="Large (230px)", command=lambda: self.change_size("large"))
        menu.add_cascade(label="📏 Size", menu=size_menu)

        mute_label = "🔊 Unmute Voice" if self.is_muted else "🔇 Mute Voice"
        menu.add_command(label=mute_label, command=self._toggle_mute)
        menu.add_command(label="🔄 Reset Position", command=self._reset_position)
        menu.add_separator()
        menu.add_command(label="❌ Exit Jarvis", command=self.close)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _trigger_listen_menu(self):
        """Manual listen trigger from context menu."""
        self.show_speech("Listening...", title="MIC ACTIVE", duration=3.0)
        self.set_state("listen")
        if self.on_trigger_listen:
            threading.Thread(target=self.on_trigger_listen, daemon=True).start()

    def _toggle_mute(self):
        """Toggle mute status."""
        self.is_muted = not self.is_muted
        if self.on_toggle_mute:
            self.on_toggle_mute()
        status_text = "Voice Muted 🔇" if self.is_muted else "Voice Active 🔊"
        self.show_speech(status_text, title="AUDIO", duration=2.0)

    def _reset_position(self):
        """Reset mascot to default bottom-right position."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - self.canvas_w - 30
        y = screen_h - self.canvas_h - 70
        self.root.geometry(f"+{x}+{y}")
        self._save_settings()

    def _open_type_dialog(self):
        """Open sleek popup to type commands silently."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Type to Jarvis")
        dialog.attributes("-topmost", True)
        dialog.config(bg="#12151c")
        dialog.resizable(False, False)

        mx = self.root.winfo_x() - 100
        my = self.root.winfo_y() + 50
        dialog.geometry(f"340x95+{max(50, mx)}+{max(50, my)}")

        lbl = tk.Label(dialog, text="Ask or command Jarvis:", bg="#12151c", fg="#00d4ff", font=("Segoe UI", 10, "bold"))
        lbl.pack(anchor="w", padx=12, pady=(10, 4))

        entry = tk.Entry(dialog, bg="#1e2330", fg="#ffffff", insertbackground="#00d4ff", font=("Segoe UI", 11), relief="flat", bd=4)
        entry.pack(fill="x", padx=12, pady=4)
        entry.focus_set()

        def submit():
            txt = entry.get().strip()
            if txt:
                dialog.destroy()
                self.show_speech(f'Thinking: "{txt}"', title="COMMAND", duration=3.0)
                if self.on_typed_command:
                    threading.Thread(target=self.on_typed_command, args=(txt,), daemon=True).start()

        entry.bind("<Return>", lambda e: submit())

    def change_size(self, size_name: str):
        """Change mascot scale."""
        if size_name not in SIZES:
            return
        self.current_size = size_name
        char_w, char_h = SIZES[self.current_size]
        self.canvas_w = max(char_w + 140, 320)
        self.canvas_h = char_h + 130
        self.char_x = self.canvas_w // 2
        self.char_y = 100 + char_h // 2

        self.root.geometry(f"{self.canvas_w}x{self.canvas_h}")
        self.canvas.config(width=self.canvas_w, height=self.canvas_h)
        self._load_images()
        self._save_settings()

    def set_state(self, state: str, text: str = "", title: str = "", duration: float = 3.5):
        """Set visual state from any thread."""
        self.event_queue.put(("SET_STATE", state, text, title, duration))

    def show_speech(self, text: str, title: str = "", duration: float = 4.0):
        """Display speech bubble above mascot."""
        self.event_queue.put(("SHOW_SPEECH", text, title, duration))

    def dance(self, duration: float = 6.5, text: str = "Check out these moves! 🕺🎶"):
        """Trigger animated dancing groove routine."""
        self.set_state("dance", text=text, title="DANCE", duration=duration)

    def thumbs_up(self, duration: float = 3.0, text: str = "Nice! 👍"):
        """Trigger thumbs-up approval gesture."""
        self.set_state("thumbsup", text=text, title="NICE!", duration=duration)

    def kneel_down(self, duration: float = 4.0, text: str = "I apologize! 🙇"):
        """Trigger remorseful kneeling-down apology pose."""
        self.set_state("kneedown", text=text, title="SORRY", duration=duration)

    def _poll_queue(self):
        """Handle incoming state events from background engine threads."""
        while not self.event_queue.empty():
            try:
                evt = self.event_queue.get_nowait()
                cmd = evt[0]

                if cmd == "SET_STATE":
                    _, new_state, text, title, duration = evt
                    self.state = new_state
                    now = time.time()
                    if text:
                        self.bubble_text = text
                        self.bubble_title = title
                        self.bubble_expire_time = now + duration

                    if new_state == "action":
                        self.action_reset_time = now + 2.5
                    elif new_state == "thumbsup":
                        self.thumbsup_reset_time = now + duration
                    elif new_state == "kneedown":
                        self.kneedown_reset_time = now + duration
                    elif new_state == "dance":
                        self.dance_reset_time = now + duration

                elif cmd == "SHOW_SPEECH":
                    _, text, title, duration = evt
                    self.bubble_text = text
                    self.bubble_title = title
                    self.bubble_expire_time = time.time() + duration

            except Exception:
                pass

        if self.is_running:
            self.root.after(25, self._poll_queue)

    def _spawn_dance_particle(self):
        """Spawn a floating musical note or sparkle particle above dancing mascot."""
        notes = ["♪", "♫", "♬", "✨", "★"]
        colors = ["#00d4ff", "#ec4899", "#a855f7", "#fbbf24", "#38bdf8"]
        char_h = SIZES[self.current_size][1]
        self.particles.append({
            "x": self.char_x + random.randint(-40, 40),
            "y": self.char_y - char_h // 2 + random.randint(-10, 20),
            "vx": random.uniform(-0.8, 0.8),
            "vy": random.uniform(-2.5, -1.2),
            "char": random.choice(notes),
            "color": random.choice(colors),
            "life": 28,
            "max_life": 28,
        })

    def _animation_loop(self):
        """Continuous render loop: enhanced movement physics, dancing routine, blinking, speech bubbles."""
        if not self.is_running:
            return

        now = time.time()
        self.anim_frame += 1

        # 1. State timeouts
        if self.state == "action" and now > self.action_reset_time:
            self.state = "idle"
        elif self.state == "thumbsup" and now > self.thumbsup_reset_time:
            self.state = "idle"
        elif self.state == "kneedown" and now > self.kneedown_reset_time:
            self.state = "idle"
        elif self.state == "dance" and now > self.dance_reset_time:
            self.state = "idle"

        # 2. Base dynamics & offsets
        x_offset = 0.0
        y_offset = 0.0
        tilt_deg = 0.0
        active_state = self.state

        # 3. Dynamic Motion Physics per State
        if self.state == "idle":
            # Multi-harmonic organic floating (zero-g astronaut buoyancy)
            y_offset = math.sin(now * 2.4) * 5.5 + math.sin(now * 1.2) * 2.0
            # Gentle horizontal organic sway
            x_offset = math.sin(now * 1.5) * 4.0
            # Gentle rotational breathing tilt
            tilt_deg = math.sin(now * 1.5) * 2.2

            # Spontaneous idle curiosity quirk (small head perk every ~14s)
            quirk_cycle = (now % 14.0)
            if quirk_cycle < 1.2:
                tilt_deg += math.sin(quirk_cycle * math.pi / 1.2) * 4.0
                y_offset -= math.sin(quirk_cycle * math.pi / 1.2) * 3.0

            # Natural periodic blinking
            if self.is_blinking:
                if now - self.last_blink_time > self.blink_duration:
                    self.is_blinking = False
                    self.last_blink_time = now + 2.0 + (time.time() % 3.0)
                else:
                    active_state = "blink"
            elif now > self.last_blink_time:
                self.is_blinking = True
                self.last_blink_time = now
                active_state = "blink"

        elif self.state == "listen":
            # Attentive forward lean toward user
            x_offset = math.sin(now * 2.0) * 2.0
            y_offset = -4.0 + math.sin(now * 3.0) * 3.0
            tilt_deg = -2.5

        elif self.state == "think":
            # Contemplative float higher with gentle orbital drift
            y_offset = -10.0 + math.sin(now * 1.8) * 4.0
            x_offset = math.cos(now * 1.8) * 3.5
            tilt_deg = math.sin(now * 1.8) * 2.0

        elif self.state == "speak":
            # Speaking mouth animation cycle
            self.speech_talk_cycle = (self.speech_talk_cycle + 1) % 12
            active_state = "speak" if self.speech_talk_cycle < 7 else "idle"
            # Energetic speech rhythm
            y_offset = math.sin(now * 7.0) * 3.5
            x_offset = math.sin(now * 3.5) * 2.0
            tilt_deg = math.sin(now * 3.5) * 2.5

        elif self.state == "action":
            # Celebratory star hop
            jump_t = max(0.0, min(1.0, (self.action_reset_time - now) / 2.5))
            y_offset = -math.sin(jump_t * math.pi * 3) * 12.0
            tilt_deg = math.sin(jump_t * math.pi * 4) * 4.0

        elif self.state == "thumbsup":
            # Upward pop bounce with confident star wink
            pop_t = max(0.0, min(1.0, (self.thumbsup_reset_time - now) / 3.0))
            y_offset = -math.sin(pop_t * math.pi * 2) * 10.0 - 4.0
            tilt_deg = 2.0

        elif self.state == "kneedown":
            # Physically drops down onto knees with humble sorrow quiver
            y_offset = 24.0
            x_offset = math.sin(now * 16.0) * 1.5
            tilt_deg = math.sin(now * 2.0) * 1.0

        elif self.state == "dance":
            # 125 BPM animated dancing groove routine!
            dance_freq = 7.5
            # Side-to-side shuffle steps
            x_offset = math.sin(now * dance_freq) * 18.0
            # Bouncy jumps on beats
            y_offset = -abs(math.cos(now * dance_freq)) * 16.0
            # Rhythm tilt back and forth
            tilt_deg = math.sin(now * dance_freq) * 7.5
            # Alternate frames dance_1 and dance_2 on beat
            active_state = "dance_1" if math.sin(now * dance_freq) >= 0 else "dance_2"

            # Spawn floating musical notes
            if self.anim_frame % 5 == 0:
                self._spawn_dance_particle()

        # 4. Clear canvas and render
        self.canvas.delete("all")
        render_x = int(self.char_x + x_offset)
        render_y = int(self.char_y + y_offset)

        # Draw Mascot Image (with cached rotation)
        img = self._get_rendered_image(active_state, tilt_deg)
        if img:
            self.canvas.create_image(render_x, render_y, image=img, anchor="center")

        # Update and render dancing note particles
        if self.particles:
            alive_particles = []
            for p in self.particles:
                p["x"] += p["vx"]
                p["y"] += p["vy"]
                p["life"] -= 1
                if p["life"] > 0:
                    fsize = max(8, int(13 * (p["life"] / p["max_life"])))
                    self.canvas.create_text(
                        int(p["x"]), int(p["y"]),
                        text=p["char"],
                        fill=p["color"],
                        font=("Segoe UI Symbol", fsize, "bold"),
                        anchor="center"
                    )
                    alive_particles.append(p)
            self.particles = alive_particles

        # Render speech bubble if active
        if self.bubble_text and now < self.bubble_expire_time:
            char_h = SIZES[self.current_size][1]
            self._render_speech_bubble(render_x, render_y - char_h // 2 - 12)

        self.root.after(33, self._animation_loop)

    def _render_speech_bubble(self, anchor_x: int, anchor_y: int):
        """Render modern rounded speech bubble above the mascot."""
        max_chars_per_line = 32
        words = self.bubble_text.split()
        lines = []
        cur_line = []
        cur_len = 0

        for w in words:
            if cur_len + len(w) + 1 <= max_chars_per_line:
                cur_line.append(w)
                cur_len += len(w) + 1
            else:
                lines.append(" ".join(cur_line))
                cur_line = [w]
                cur_len = len(w)
        if cur_line:
            lines.append(" ".join(cur_line))

        if len(lines) > 4:
            lines = lines[:4]
            lines[3] += "..."

        bubble_text = "\n".join(lines)
        num_lines = len(lines)

        bubble_w = min(self.canvas_w - 20, max(140, max(len(l) for l in lines) * 7 + 30))
        bubble_h = max(38, num_lines * 16 + 22)

        bx1 = max(10, anchor_x - bubble_w // 2)
        bx2 = bx1 + bubble_w
        by2 = max(45, anchor_y)
        by1 = max(8, by2 - bubble_h)

        # Border color based on visual state
        if self.state == "listen":
            border_col = "#00d4ff"
        elif self.state == "think":
            border_col = "#a855f7"
        elif self.state == "action":
            border_col = "#10b981"
        elif self.state == "thumbsup":
            border_col = "#f59e0b"
        elif self.state == "kneedown":
            border_col = "#38bdf8"
        elif self.state == "dance":
            border_col = "#ec4899"
        else:
            border_col = "#38bdf8"

        bg_col = "#0d1117"
        r = 10

        # Draw rounded bubble polygon
        self.canvas.create_polygon(
            bx1 + r, by1, bx2 - r, by1, bx2, by1, bx2, by1 + r,
            bx2, by2 - r, bx2, by2, bx2 - r, by2, bx1 + r, by2,
            bx1, by2, bx1, by2 - r, bx1, by1 + r, bx1, by1,
            smooth=True, fill=bg_col, outline=border_col, width=2
        )

        # Pointer tail pointing towards the robot head
        tail_x = anchor_x
        self.canvas.create_polygon(
            tail_x - 7, by2 - 1, tail_x + 7, by2 - 1, tail_x, by2 + 8,
            fill=bg_col, outline=border_col, width=1
        )

        # Optional Title (e.g. LISTENING, DANCE, NICE, SORRY)
        if self.bubble_title:
            self.canvas.create_text(
                (bx1 + bx2) // 2, by1 + 10,
                text=self.bubble_title,
                fill=border_col,
                font=("Segoe UI", 7, "bold"),
                anchor="center"
            )
            text_y = by1 + 12 + (by2 - by1) // 2
        else:
            text_y = (by1 + by2) // 2

        # Text inside bubble
        self.canvas.create_text(
            (bx1 + bx2) // 2, text_y,
            text=bubble_text,
            fill="#ffffff",
            font=("Segoe UI", 9),
            justify="center",
            anchor="center"
        )

    def close(self):
        """Safely destroy the mascot window."""
        self.is_running = False
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        """Start the Tkinter main loop."""
        try:
            self.root.mainloop()
        except Exception as e:
            logger.error("Mascot mainloop error: %s", e)
