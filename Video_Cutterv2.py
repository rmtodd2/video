import os
import sys
import time
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Requires:
#   pip install python-vlc
# And VLC installed on the system.
import vlc


def hms_to_seconds(s: str) -> float:
    """
    Accepts:
      SS
      MM:SS
      HH:MM:SS
      with optional .ms
    Examples:
      90
      01:30
      00:01:30.500
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("Empty time")

    parts = s.split(":")
    if len(parts) == 1:
        return float(parts[0])

    parts = [p.strip() for p in parts]
    if len(parts) == 2:
        mm = float(parts[0])
        ss = float(parts[1])
        return mm * 60 + ss
    if len(parts) == 3:
        hh = float(parts[0])
        mm = float(parts[1])
        ss = float(parts[2])
        return hh * 3600 + mm * 60 + ss

    raise ValueError("Time must be SS, MM:SS, or HH:MM:SS(.ms)")


def seconds_to_hms(sec: float) -> str:
    if sec < 0:
        sec = 0
    hh = int(sec // 3600)
    mm = int((sec % 3600) // 60)
    ss = sec % 60
    if hh > 0:
        return f"{hh:02d}:{mm:02d}:{ss:06.3f}".rstrip("0").rstrip(".")
    return f"{mm:02d}:{ss:06.3f}".rstrip("0").rstrip(".")


class VideoCutterGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Video Cutter (Preview + Set Points)")

        # Default window size similar to your screenshot
        self.root.geometry("950x980")
        self.root.minsize(820, 700)

        # VLC
        #self.vlc_instance = vlc.Instance()
        self.vlc_instance = vlc.Instance("--quiet", "--no-plugins-cache")
        self.player = self.vlc_instance.media_player_new()
        self.media = None

        # State
        self.input_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.output_name = tk.StringVar(value="cut_clip.mp4")

        self.start_time_str = tk.StringVar()
        self.end_time_str = tk.StringVar()

        self.status = tk.StringVar(value="Ready.")
        self.is_playing = False
        self.user_dragging_seek = False

        # UI
        self._build_ui()
        self._bind_close()
        self._poll_player()

    # ---------- UI ----------
    def _build_ui(self):
        # Top: file selection
        top = ttk.Frame(self.root, padding=10)
        top.grid(row=0, column=0, sticky="nsew")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        ttk.Label(top, text="Input Video:").grid(row=0, column=0, sticky="e")
        ttk.Entry(top, textvariable=self.input_path, width=60).grid(row=0, column=1, padx=6, sticky="we")
        ttk.Button(top, text="Browse…", command=self.browse_file).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(top, text="Load", command=self.load_video).grid(row=0, column=3)
        top.columnconfigure(1, weight=1)

        # Player area
        mid = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        mid.grid(row=1, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        self.video_panel = tk.Frame(mid, bg="black", height=360)
        self.video_panel.grid(row=0, column=0, sticky="nsew")

        # Controls (left buttons + right jump group)
        controls = ttk.Frame(mid)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(0, weight=1)  # push right_controls to the far right

        left_controls = ttk.Frame(controls)
        left_controls.grid(row=0, column=0, sticky="w")

        right_controls = ttk.Frame(controls)
        right_controls.grid(row=0, column=1, sticky="e")

        # Left side buttons
        self.play_btn = ttk.Button(left_controls, text="Play", command=self.toggle_play)
        self.play_btn.grid(row=0, column=0, padx=(0, 6))

        ttk.Button(left_controls, text="⏪ -5s", command=lambda: self.seek_relative(-5)).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(left_controls, text="+5s ⏩", command=lambda: self.seek_relative(5)).grid(row=0, column=2, padx=(0, 6))

        # NEW: +/- 60s buttons to the right of the 5s buttons
        ttk.Button(left_controls, text="⏪ -60s", command=lambda: self.seek_relative(-60)).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(left_controls, text="+60s ⏩", command=lambda: self.seek_relative(60)).grid(row=0, column=4, padx=(0, 10))

        # Right side jump controls (move together with Go)
        ttk.Label(right_controls, text="Jump to:").grid(row=0, column=0, sticky="e")
        self.jump_entry = ttk.Entry(right_controls, width=14)
        self.jump_entry.grid(row=0, column=1, sticky="w", padx=(6, 6))
        ttk.Button(right_controls, text="Go", command=self.jump_to_time).grid(row=0, column=2)

        # Seek bar + time
        seekrow = ttk.Frame(mid)
        seekrow.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        seekrow.columnconfigure(0, weight=1)

        self.seek = ttk.Scale(seekrow, from_=0, to=1000, orient="horizontal", command=self._on_seek_drag)
        self.seek.grid(row=0, column=0, sticky="ew")
        self.seek.bind("<ButtonPress-1>", self._on_seek_press)
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)

        self.time_label = ttk.Label(seekrow, text="00:00 / 00:00")
        self.time_label.grid(row=0, column=1, padx=(8, 0))

        # Cut Points (keep buttons next to entries; no expanding columns here)
        points = ttk.Labelframe(self.root, text="Cut Points", padding=10)
        points.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

        ttk.Label(points, text="Start:").grid(row=0, column=0, sticky="e")
        ttk.Entry(points, textvariable=self.start_time_str, width=18).grid(row=0, column=1, sticky="w", padx=(6, 6))
        ttk.Button(points, text="Set Start (current)", command=self.set_start_from_current).grid(row=0, column=2, padx=(0, 12))

        ttk.Label(points, text="End:").grid(row=0, column=3, sticky="e")
        ttk.Entry(points, textvariable=self.end_time_str, width=18).grid(row=0, column=4, sticky="w", padx=(6, 6))
        ttk.Button(points, text="Set End (current)", command=self.set_end_from_current).grid(row=0, column=5)

        ttk.Label(points, text="(Times: SS, MM:SS, or HH:MM:SS[.ms])").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0)
        )

        # Output options (both entries same width and stretch)
        out = ttk.Labelframe(self.root, text="Output", padding=10)
        out.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        out.columnconfigure(1, weight=1)

        ttk.Label(out, text="Output folder:").grid(row=0, column=0, sticky="e")
        ttk.Entry(out, textvariable=self.output_dir).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(out, text="Choose…", command=self.choose_output_folder).grid(row=0, column=2)

        ttk.Label(out, text="Output filename:").grid(row=1, column=0, sticky="e", pady=(8, 0))
        ttk.Entry(out, textvariable=self.output_name).grid(row=1, column=1, sticky="we", padx=6, pady=(8, 0))

        self.reencode = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            out,
            text="Re-encode for frame-accurate cuts (slower, larger files)",
            variable=self.reencode
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ttk.Button(self.root, text="Create New Clip", command=self.cut_video).grid(row=4, column=0, pady=(0, 10))

        # Status
        statusbar = ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=6)
        statusbar.grid(row=5, column=0, sticky="ew")

    def _bind_close(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- File / Load ----------
    def browse_file(self):
        file_path = filedialog.askopenfilename(
            title="Select a video",
            filetypes=[
                ("All files", "*.*"),
                ("Video files", "*.mp4 *.mkv *.mov *.avi *.wmv *.webm *.m4v *.mpeg *.mpg *.flv"),
            ],
        )
        if file_path:
            self.input_path.set(file_path)

    def choose_output_folder(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_dir.set(folder)

    def load_video(self):
        path = self.input_path.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", "Please choose a valid input video file.")
            return

        if not self.output_dir.get().strip():
            self.output_dir.set(os.path.dirname(path))

        base = os.path.splitext(os.path.basename(path))[0]
        ext = os.path.splitext(path)[1] or ".mp4"
        self.output_name.set(f"{base}_clip{ext}")

        self.media = self.vlc_instance.media_new(path)
        self.player.set_media(self.media)

        self._set_vlc_window_handle()

        self.player.play()
        time.sleep(0.1)
        self.player.pause()
        self.is_playing = False
        self.play_btn.configure(text="Play")
        self.status.set("Loaded. Use Play and seek controls to choose start/end points.")

    def _set_vlc_window_handle(self):
        self.root.update_idletasks()
        handle = self.video_panel.winfo_id()

        if sys.platform.startswith("win"):
            self.player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self.player.set_nsobject(handle)
        else:
            self.player.set_xwindow(handle)

    # ---------- Player Controls ----------
    def toggle_play(self):
        if not self.media:
            self.load_video()
            return

        if self.is_playing:
            self.player.pause()
            self.is_playing = False
            self.play_btn.configure(text="Play")
        else:
            self.player.play()
            self.is_playing = True
            self.play_btn.configure(text="Pause")

    def seek_relative(self, seconds: float):
        if not self.media:
            return
        cur_ms = self.player.get_time()
        if cur_ms < 0:
            return
        new_ms = max(0, cur_ms + int(seconds * 1000))
        self.player.set_time(new_ms)

    def jump_to_time(self):
        if not self.media:
            return
        s = self.jump_entry.get().strip()
        try:
            sec = hms_to_seconds(s)
        except Exception:
            messagebox.showerror("Invalid time", "Enter SS, MM:SS, or HH:MM:SS(.ms).")
            return
        self.player.set_time(int(sec * 1000))

    def set_start_from_current(self):
        if not self.media:
            return
        ms = self.player.get_time()
        if ms < 0:
            return
        self.start_time_str.set(seconds_to_hms(ms / 1000.0))

    def set_end_from_current(self):
        if not self.media:
            return
        ms = self.player.get_time()
        if ms < 0:
            return
        self.end_time_str.set(seconds_to_hms(ms / 1000.0))

    # Seek bar handlers
    def _on_seek_press(self, _evt):
        self.user_dragging_seek = True

    def _on_seek_release(self, _evt):
        if not self.media:
            self.user_dragging_seek = False
            return
        length = self.player.get_length()
        if length > 0:
            frac = float(self.seek.get()) / 1000.0
            self.player.set_time(int(frac * length))
        self.user_dragging_seek = False

    def _on_seek_drag(self, _val):
        # Keep simple: set time on release
        pass

    def _poll_player(self):
        try:
            if self.media:
                length = self.player.get_length()
                cur = self.player.get_time()

                if length > 0 and cur >= 0:
                    if not self.user_dragging_seek:
                        frac = cur / float(length)
                        self.seek.set(int(frac * 1000))

                    self.time_label.configure(
                        text=f"{seconds_to_hms(cur/1000.0)} / {seconds_to_hms(length/1000.0)}"
                    )
                else:
                    self.time_label.configure(text="00:00 / 00:00")
        except Exception:
            pass

        self.root.after(200, self._poll_player)

    # ---------- Cutting ----------
    def cut_video(self):
        input_path = self.input_path.get().strip()
        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("Error", "Please select a valid input video.")
            return

        out_dir = self.output_dir.get().strip()
        if not out_dir:
            messagebox.showerror("Error", "Please choose an output folder.")
            return
        if not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Output folder does not exist.")
            return

        out_name = self.output_name.get().strip()
        if not out_name:
            messagebox.showerror("Error", "Please enter an output filename.")
            return

        output_path = os.path.join(out_dir, out_name)

        start_s = self.start_time_str.get().strip()
        end_s = self.end_time_str.get().strip()
        if not start_s or not end_s:
            messagebox.showerror("Error", "Please set both Start and End times.")
            return

        try:
            start_sec = hms_to_seconds(start_s)
            end_sec = hms_to_seconds(end_s)
        except Exception as e:
            messagebox.showerror("Error", f"Invalid time format.\n{e}")
            return

        if end_sec <= start_sec:
            messagebox.showerror("Error", "End time must be greater than Start time.")
            return

        if self.reencode.get():
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss", str(start_sec),
                "-to", str(end_sec),
                "-i", input_path,
                "-c:v", "libx264",
                "-c:a", "aac",
                "-movflags", "+faststart",
                output_path
            ]
        else:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss", str(start_sec),
                "-to", str(end_sec),
                "-i", input_path,
                "-c", "copy",
                output_path
            ]

        self.status.set("Cutting… (ffmpeg running)")
        self.root.update_idletasks()

        try:
            subprocess.run(cmd, check=True)
            self.status.set(f"Done. Saved: {output_path}")
            messagebox.showinfo("Success", f"New clip created:\n{output_path}\n\nOriginal file unchanged.")
        except FileNotFoundError:
            self.status.set("Error: ffmpeg not found.")
            messagebox.showerror("Error", "ffmpeg not found. Install ffmpeg and ensure it is in your PATH.")
        except subprocess.CalledProcessError:
            self.status.set("Error: ffmpeg failed.")
            messagebox.showerror(
                "Error",
                "Failed to create clip.\n\nTip: If your cut is inaccurate or fails with copy mode, "
                "check 'Re-encode for frame-accurate cuts'."
            )

    def _on_close(self):
        try:
            if self.player:
                self.player.stop()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()

    # Nicer scaling on Windows (optional)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = VideoCutterGUI(root)
    root.mainloop()