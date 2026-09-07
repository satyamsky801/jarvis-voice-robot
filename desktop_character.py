"""
JARVIS Desktop Mascot Companion
An interactive, animated 3D robot character that floats on your Windows desktop,
reacts in real-time to voice commands, LLM thinking, speech, and PC task execution.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
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

        # Mascot state
        self.state = "idle"  # idle, blink, listen, think, speak, action
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

        # Animation timing
        self.anim_frame = 0
        self.last_blink_time = time.time()
        self.blink_duration = 0.2
        self.is_blinking = False
        self.action_reset_time = 0.0
        self.speech_talk_cycle = 0

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

        # Start animation and event loop
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
        """Pre-cache resized images for fast rendering."""
        target_size = SIZES[self.current_size]
        self.images = {}
        for state_name in ["idle", "blink", "listen", "think", "speak", "action"]:
            img_path = ASSETS_DIR / f"mascot_{state_name}.png"
            if not img_path.exists():
                img_path = ASSETS_DIR / "mascot_idle.png"

            try:
                raw_img = Image.open(img_path).convert("RGBA")
                resized = raw_img.resize(target_size, Image.Resampling.LANCZOS)

                # Composite onto transparent key color so borders are smooth
                bg = Image.new("RGBA", resized.size, (1, 1, 1, 255))
                comp = Image.alpha_composite(bg, resized)
                self.images[state_name] = ImageTk.PhotoImage(comp)
            except Exception as e:
                logger.error("Failed to load mascot image %s: %s", state_name, e)

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

        # Center near mascot
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
                self.show_speech(f"Thinking: \"{txt}\"", title="COMMAND", duration=3.0)
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

    def _poll_queue(self):
        """Handle incoming state events from background engine threads."""
        while not self.event_queue.empty():
            try:
                evt = self.event_queue.get_nowait()
                cmd = evt[0]

                if cmd == "SET_STATE":
                    _, new_state, text, title, duration = evt
                    self.state = new_state
                    if text:
                        self.bubble_text = text
                        self.bubble_title = title
                        self.bubble_expire_time = time.time() + duration
                    if new_state == "action":
                        self.action_reset_time = time.time() + 2.5

                elif cmd == "SHOW_SPEECH":
                    _, text, title, duration = evt
                    self.bubble_text = text
                    self.bubble_title = title
                    self.bubble_expire_time = time.time() + duration

            except Exception:
                pass

        if self.is_running:
            self.root.after(25, self._poll_queue)

    def _animation_loop(self):
        """Continuous render loop: floating physics, blinking, speech bubbles."""
        if not self.is_running:
            return

        now = time.time()
        self.anim_frame += 1

        # 1. Action state timeout
        if self.state == "action" and now > self.action_reset_time:
            self.state = "idle"

        # 2. Random natural blinking in idle state
        active_state = self.state
        if self.state == "idle":
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

        # 3. Speaking mouth animation cycle
        if self.state == "speak":
            self.speech_talk_cycle = (self.speech_talk_cycle + 1) % 12
            active_state = "speak" if self.speech_talk_cycle < 7 else "idle"

        # 4. Floating vertical bob physics: y = y_base + sin(t) * 5
        bob_offset = int(math.sin(now * 2.6) * 5.0)
        render_y = self.char_y + bob_offset

        # 5. Clear and render canvas
        self.canvas.delete("all")

        # Render active image
        img = self.images.get(active_state, self.images.get("idle"))
        if img:
            self.canvas.create_image(self.char_x, render_y, image=img, anchor="center")

        # Render speech bubble if active
        if self.bubble_text and now < self.bubble_expire_time:
            self._render_speech_bubble(self.char_x, render_y - SIZES[self.current_size][1] // 2 - 12)

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

        # Max 4 lines shown in bubble
        if len(lines) > 4:
            lines = lines[:4]
            lines[3] += "..."

        bubble_text = "\n".join(lines)
        num_lines = len(lines)

        bubble_w = min(self.canvas_w - 20, max(140, max(len(l) for l in lines) * 7 + 30))
        bubble_h = max(38, num_lines * 16 + 22)

        # Bubble coordinates (clamped inside canvas)
        bx1 = max(10, self.char_x - bubble_w // 2)
        bx2 = bx1 + bubble_w
        by2 = max(45, anchor_y)
        by1 = max(8, by2 - bubble_h)

        # Border color based on state
        border_col = "#00d4ff" if self.state == "listen" else ("#a855f7" if self.state == "think" else ("#10b981" if self.state == "action" else "#38bdf8"))
        bg_col = "#0d1117"

        # Draw rounded bubble container
        r = 10
        self.canvas.create_polygon(
            bx1 + r, by1, bx2 - r, by1, bx2, by1, bx2, by1 + r,
            bx2, by2 - r, bx2, by2, bx2 - r, by2, bx1 + r, by2,
            bx1, by2, bx1, by2 - r, bx1, by1 + r, bx1, by1,
            smooth=True, fill=bg_col, outline=border_col, width=2
        )

        # Little pointer tail pointing to the robot head
        tail_x = self.char_x
        self.canvas.create_polygon(
            tail_x - 7, by2 - 1, tail_x + 7, by2 - 1, tail_x, by2 + 8,
            fill=bg_col, outline=border_col, width=1
        )

        # Optional Title (e.g. LISTENING, COMMAND)
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

        # Spoken / Recognized text
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
