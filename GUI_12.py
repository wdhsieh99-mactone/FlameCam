import json
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime

from PIL import Image, ImageTk
from libcamera import Transform
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

PREVIEW_SIZE = (640, 360)
MAIN_SIZE = (1920, 1080)
PREVIEW_INTERVAL_MS = 66  # ~15 fps
THUMB_SIZE = (96, 54)
THUMBS_PER_ROW = 6

# Hardcoded fallback used to seed a fresh settings file, to fill in any key
# missing/invalid in a saved or hand-edited settings file, and as the target
# of the Settings tab's "Restore Factory Defaults" button. The Settings tab
# lets a user reconfigure all of this at runtime (see load_settings/
# save_settings/parse_settings_form below) - these are just the day-one values.
FACTORY_PRESETS = {
    "blend_gas": ["H2", "NH3"],
    "blend_ratio": [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    "phi": [0.85, 0.9, 0.95],
    "lance_depth": [10, 50, 100, 150, 200],
    "lance_size": [4, 6, 8],
}
FACTORY_DEFAULTS = {
    "blend_gas": "H2",
    "blend_ratio": 0,
    "phi": 0.85,
    "lance_depth": 10,
    "lance_size": 4,
    "kw": "",
    "shutter": 10000,
    "gain": 10.0,
    "awb_r": 2.0,
    "awb_b": 2.3,
    "lens": 1.25,
    "duration": 10,
    "n_snaps": 5,
}
FACTORY_RANGES = {
    "shutter": [100, 32000],
    "gain": [1.0, 16.0],
    "lens": [0.0, 10.0],
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui12_settings.json")

# ---------------------------------------------------------------------- #
# Look & feel: one neutral light palette + accent, applied consistently
# across every widget-building method below (via ttk.Style for Frames/
# Labels/Notebook, and style_button/style_entry for the plain tk widgets
# that need to keep tk-only features - Scale's `resolution`, Entry's
# state read-back matching the existing tests/automation).
# ---------------------------------------------------------------------- #
COLORS = {
    "bg": "#eef1f6",
    "surface": "#ffffff",
    "border": "#d7dce3",
    "text": "#1f2937",
    "text_muted": "#6b7280",
    "primary": "#2f6fed",
    "primary_dark": "#1d4fc4",
    "primary_light": "#e8f0fe",
    "danger": "#dc2626",
    "danger_dark": "#a91c1c",
    "header_bg": "#1f2b45",
}

FONT_FAMILY = "Helvetica"
FONT_BASE = (FONT_FAMILY, 10)
FONT_HEADER = (FONT_FAMILY, 17, "bold")
FONT_SUBHEADER = (FONT_FAMILY, 11)
FONT_SECTION = (FONT_FAMILY, 11, "bold")
FONT_BUTTON = (FONT_FAMILY, 10, "bold")

_BUTTON_STYLES = {
    "primary": dict(bg=COLORS["primary"], fg="white", activebackground=COLORS["primary_dark"],
                     activeforeground="white", disabledforeground="#c7d5fa"),
    "danger": dict(bg=COLORS["danger"], fg="white", activebackground=COLORS["danger_dark"],
                    activeforeground="white", disabledforeground="#f3c2c2"),
    "secondary": dict(bg=COLORS["surface"], fg=COLORS["text"], activebackground=COLORS["primary_light"],
                       activeforeground=COLORS["primary"], disabledforeground=COLORS["text_muted"]),
}


def style_button(button, kind="secondary"):
    button.configure(
        relief="flat", bd=0, cursor="hand2", font=FONT_BUTTON, padx=12, pady=6,
        highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["border"],
        **_BUTTON_STYLES[kind],
    )


def style_entry(entry):
    entry.configure(
        relief="flat", bd=1, highlightthickness=1,
        highlightbackground=COLORS["border"], highlightcolor=COLORS["primary"],
        bg="white", fg=COLORS["text"], font=FONT_BASE,
        disabledbackground=COLORS["bg"], disabledforeground=COLORS["text_muted"],
        readonlybackground=COLORS["primary_light"],
    )


def _fmt_num(value):
    """str() a number the way the old hardcoded string defaults looked:
    whole-valued floats (10.0) print as "10", not "10.0"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_settings_form(raw):
    """Pure function: raw Entry-widget strings from the Settings tab ->
    validated, typed (presets, defaults, ranges) dicts, or raises ValueError
    with a message naming the offending field. Kept free of Tk/camera so it
    can be unit-tested directly - deliberately mirrors validate-on-load below.
    """
    def parse_list(text, caster, label):
        items = []
        for piece in text.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                items.append(caster(piece))
            except ValueError:
                raise ValueError(f"{label}: '{piece}' is not a valid number")
        if not items:
            raise ValueError(f"{label}: at least one value is required")
        return items

    def parse_num(text, caster, label):
        try:
            return caster(text.strip())
        except ValueError:
            raise ValueError(f"{label}: '{text}' is not a valid number")

    presets = {}
    defaults = {}
    ranges = {}

    presets["blend_gas"] = [p.strip() for p in raw["blend_gas_values"].split(",") if p.strip()]
    if not presets["blend_gas"]:
        raise ValueError("Blending gas: at least one value is required")
    defaults["blend_gas"] = raw["blend_gas_default"].strip()
    if defaults["blend_gas"] not in presets["blend_gas"]:
        raise ValueError(f"Blending gas: default '{defaults['blend_gas']}' is not in the values list")

    for key, label, caster in (
        ("blend_ratio", "Blending ratio (%)", int),
        ("phi", "Phi", float),
        ("lance_depth", "Lance depth (mm)", int),
        ("lance_size", "Lance size (mm)", int),
    ):
        values = parse_list(raw[f"{key}_values"], caster, label)
        default_ = parse_num(raw[f"{key}_default"], caster, label)
        if default_ not in values:
            raise ValueError(f"{label}: default {default_} is not in the values list")
        presets[key] = values
        defaults[key] = default_

    for key, label, caster in (
        ("shutter", "Shutter (us)", int),
        ("gain", "Gain", float),
        ("lens", "Lens position", float),
    ):
        lo = parse_num(raw[f"{key}_min"], caster, f"{label} min")
        hi = parse_num(raw[f"{key}_max"], caster, f"{label} max")
        default_ = parse_num(raw[f"{key}_default"], caster, f"{label} default")
        if not lo < hi:
            raise ValueError(f"{label}: min must be less than max")
        if not lo <= default_ <= hi:
            raise ValueError(f"{label}: default {default_} must be between {lo} and {hi}")
        ranges[key] = [lo, hi]
        defaults[key] = default_

    defaults["kw"] = raw["kw_default"].strip()
    if defaults["kw"]:
        try:
            float(defaults["kw"])
        except ValueError:
            raise ValueError("kW default: must be numeric or blank")

    defaults["awb_r"] = parse_num(raw["awb_r_default"], float, "AWB red gain default")
    defaults["awb_b"] = parse_num(raw["awb_b_default"], float, "AWB blue gain default")
    defaults["duration"] = parse_num(raw["duration_default"], float, "Duration default")
    if defaults["duration"] <= 0:
        raise ValueError("Duration default: must be positive")
    defaults["n_snaps"] = parse_num(raw["n_snaps_default"], int, "Snapshots default")
    if defaults["n_snaps"] < 0:
        raise ValueError("Snapshots default: must be zero or more")

    return presets, defaults, ranges


def load_settings():
    """Load CONFIG_PATH, validating each field independently against the
    same rules as parse_settings_form (non-empty list, default-in-list,
    min<max, default-in-range). A field that's missing OR present-but-invalid
    falls back to its factory value individually, with a warning collected
    for the caller to show - one bad hand-edited field shouldn't nuke the
    rest of an otherwise-good settings file.
    """
    presets = {k: list(v) for k, v in FACTORY_PRESETS.items()}
    defaults = dict(FACTORY_DEFAULTS)
    ranges = {k: list(v) for k, v in FACTORY_RANGES.items()}
    warnings = []

    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            warnings.append(f"Could not read settings file ({exc}); using factory defaults.")
            data = {}

    loaded_presets = data.get("presets", {})
    loaded_defaults = data.get("defaults", {})
    loaded_ranges = data.get("ranges", {})

    for key in FACTORY_PRESETS:
        if key not in loaded_presets:
            continue
        try:
            values = loaded_presets[key]
            if not isinstance(values, list) or not values:
                raise ValueError("must be a non-empty list")
            default_ = loaded_defaults.get(key, FACTORY_DEFAULTS[key])
            if default_ not in values:
                raise ValueError(f"default {default_!r} not in saved values {values!r}")
            presets[key] = values
            defaults[key] = default_
        except (ValueError, TypeError) as exc:
            warnings.append(f"Ignoring saved '{key}' preset ({exc}); using factory default.")

    for key in FACTORY_RANGES:
        if key not in loaded_ranges:
            continue
        try:
            lo, hi = loaded_ranges[key]
            if not lo < hi:
                raise ValueError("min must be less than max")
            default_ = loaded_defaults.get(key, FACTORY_DEFAULTS[key])
            if not lo <= default_ <= hi:
                raise ValueError(f"default {default_!r} outside [{lo}, {hi}]")
            ranges[key] = [lo, hi]
            defaults[key] = default_
        except (ValueError, TypeError) as exc:
            warnings.append(f"Ignoring saved '{key}' range ({exc}); using factory default.")

    for key in ("kw", "awb_r", "awb_b", "duration", "n_snaps"):
        if key in loaded_defaults:
            defaults[key] = loaded_defaults[key]

    return presets, defaults, ranges, warnings


def save_settings(presets, defaults, ranges):
    data = {"presets": presets, "defaults": defaults, "ranges": ranges}
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


class CameraTestGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ReactingFlow Lab - Camera Controller")
        self.root.geometry("1400x800")

        self.presets, self.defaults, self.ranges, load_warnings = load_settings()

        self.blend_gas_var = tk.StringVar(value=str(self.defaults["blend_gas"]))
        self.blend_var = tk.StringVar(value=str(self.defaults["blend_ratio"]))
        self.phi_var = tk.StringVar(value=str(self.defaults["phi"]))
        self.lance_depth_var = tk.StringVar(value=str(self.defaults["lance_depth"]))
        self.lance_size_var = tk.StringVar(value=str(self.defaults["lance_size"]))
        self.kw_var = tk.StringVar(value=str(self.defaults["kw"]))

        self.shutter_var = tk.IntVar(value=int(self.defaults["shutter"]))
        self.gain_var = tk.DoubleVar(value=float(self.defaults["gain"]))
        self.awb_r_var = tk.DoubleVar(value=float(self.defaults["awb_r"]))
        self.awb_b_var = tk.DoubleVar(value=float(self.defaults["awb_b"]))
        self.lens_var = tk.DoubleVar(value=float(self.defaults["lens"]))

        self.duration_var = tk.StringVar(value=_fmt_num(self.defaults["duration"]))
        self.n_snaps_var = tk.StringVar(value=str(self.defaults["n_snaps"]))
        self.status_var = tk.StringVar(value="")

        self.picam2 = None
        self.camera_ready = False
        self.preview_active = False
        # Bumped on every preview start/stop. A frame the camera thread
        # captured just before a stop can still be sitting in _gui_queue when
        # the panel gets reset to black; tagging frames with the epoch active
        # at capture time lets _render_preview_frame discard such stale
        # frames instead of silently reviving the image after the reset.
        self._preview_epoch = 0
        self._preview_photo = None
        self._recording_in_progress = False
        self._abort_recording = False

        self.control_widgets = []

        # Worker threads must never touch Tkinter widgets or call root.after()
        # directly (Tcl's command registration isn't thread-safe here and raises
        # "main thread is not in main loop"). They post (callable, args) tuples
        # onto this queue instead, which only the main thread drains.
        self._gui_queue = queue.Queue()

        # ALL picam2 calls (preview capture, control updates, recording) are
        # funnelled through one persistent background thread for the entire
        # app lifetime - never called from the main thread once it starts.
        # This was forced by hard empirical testing: calling capture_array()
        # from a *different* thread than the one that most recently called
        # stop_recording() hangs forever on this hardware/picamera2 build,
        # even well after stop_recording() has returned (not just while it's
        # running). Funnelling everything through a single thread sidesteps
        # that entirely - see _camera_thread_fn.
        self._camera_cmd_queue = queue.Queue()
        self._camera_thread = None
        self._shutdown_event = threading.Event()

        self.create_widgets()
        self.init_camera()
        self._poll_gui_queue()

        if load_warnings:
            messagebox.showwarning("Settings", "\n".join(load_warnings))

    # ---------------------------------------------------------------- #
    # Widget construction
    # ---------------------------------------------------------------- #
    def _setup_style(self):
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=FONT_BASE)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=FONT_BASE)
        style.configure(
            "TLabelframe", background=COLORS["surface"], bordercolor=COLORS["border"],
            relief="solid", borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["text"], font=FONT_SECTION,
        )
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab", background=COLORS["bg"], foreground=COLORS["text_muted"],
            font=FONT_SECTION, padding=(18, 10), borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["surface"])],
            foreground=[("selected", COLORS["primary"])],
        )

    def create_widgets(self):
        self._setup_style()

        header = tk.Frame(self.root, bg=COLORS["header_bg"])
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=COLORS["header_bg"])
        header_inner.pack(fill="x", padx=20, pady=14)

        tk.Label(
            header_inner, text="ReactingFlow Lab", bg=COLORS["header_bg"], fg="white", font=FONT_HEADER,
        ).pack(side="left")
        tk.Label(
            header_inner, text="  Camera Test Controller", bg=COLORS["header_bg"], fg="#9fb3d9",
            font=FONT_SUBHEADER,
        ).pack(side="left", padx=(4, 0))

        try:
            self.img = tk.PhotoImage(file="ReFlowLab_signature.gif")
            tk.Label(header_inner, image=self.img, bg=COLORS["header_bg"]).pack(side="right")
        except tk.TclError:
            self.img = None

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        tab_camera = ttk.Frame(self.notebook)
        tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(tab_camera, text="Camera Test")
        self.notebook.add(tab_settings, text="Settings")

        Frame_main = ttk.Frame(tab_camera)
        Frame_main.pack(fill="both", expand=True)

        Frame_left = ttk.Frame(Frame_main)
        Frame_left.pack(side="left", fill="y", padx=15, pady=15)

        Frame_right = ttk.Frame(Frame_main)
        Frame_right.pack(side="left", fill="both", expand=True, padx=(0, 15), pady=15)

        self._build_condition_frame(Frame_left)
        self._build_controls_frame(Frame_left)
        self._build_preview_frame(Frame_right)

        self.frame_actions = ttk.Frame(tab_camera)
        self.frame_actions.pack(fill="x", padx=15, pady=(0, 15))
        self._build_action_frame(self.frame_actions)

        self._build_settings_tab(tab_settings)

    def _build_condition_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Test condition", padding=12)
        frame.pack(fill="x", pady=(0, 12))

        def add_picklist_row(row, label, key, var):
            tk.Label(
                frame, text=label, width=16, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
            ).grid(row=row, column=0, sticky="w", pady=4)
            entry = tk.Entry(frame, textvariable=var, width=12, justify="center", state="readonly")
            style_entry(entry)
            entry.grid(row=row, column=1, padx=6)
            # Looks up self.presets[key] at click time (not bound at row-build
            # time) so a Settings-tab Apply that reassigns self.presets is
            # picked up on the very next click without rebuilding this row.
            button = tk.Button(
                frame, text="Select", width=8,
                command=lambda key=key, label=label, var=var: self.show_picklist(
                    f"Select {label}", self.presets[key], var
                ),
            )
            style_button(button, "secondary")
            button.grid(row=row, column=2, padx=6)
            self.control_widgets.append(button)

        add_picklist_row(0, "Blending gas", "blend_gas", self.blend_gas_var)
        add_picklist_row(1, "Blending ratio (%)", "blend_ratio", self.blend_var)
        add_picklist_row(2, "Phi", "phi", self.phi_var)
        add_picklist_row(3, "Lance depth (mm)", "lance_depth", self.lance_depth_var)
        add_picklist_row(4, "Lance size (mm)", "lance_size", self.lance_size_var)

        tk.Label(
            frame, text="kW", width=16, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
        ).grid(row=5, column=0, sticky="w", pady=4)
        kw_entry = tk.Entry(frame, textvariable=self.kw_var, width=12, justify="center")
        style_entry(kw_entry)
        kw_entry.grid(row=5, column=1, padx=6)
        self.control_widgets.append(kw_entry)

    def _build_controls_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Camera controls", padding=12)
        frame.pack(fill="x", pady=(0, 12))

        def add_slider_row(row, label, var, lo, hi, resolution, attr_name):
            tk.Label(
                frame, text=label, width=16, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
            ).grid(row=row, column=0, sticky="w", pady=4)
            scale = tk.Scale(
                frame, from_=lo, to=hi, resolution=resolution, orient="horizontal",
                variable=var, length=200, showvalue=0, command=self._apply_controls,
                bg=COLORS["surface"], troughcolor="#c3c9d4", fg=COLORS["primary"],
                activebackground=COLORS["primary"], highlightthickness=0, bd=0,
                sliderrelief="flat",
            )
            scale.grid(row=row, column=1, padx=6)
            entry = tk.Entry(frame, textvariable=var, width=8, justify="center")
            style_entry(entry)
            entry.grid(row=row, column=2, padx=6)
            entry.bind("<Return>", self._apply_controls)
            entry.bind("<FocusOut>", self._apply_controls)
            self.control_widgets.extend([scale, entry])
            # Kept so the Settings tab can reconfigure from_/to live (see
            # _reconfigure_ranges) without rebuilding this row.
            setattr(self, attr_name, scale)

        shutter_lo, shutter_hi = self.ranges["shutter"]
        gain_lo, gain_hi = self.ranges["gain"]
        lens_lo, lens_hi = self.ranges["lens"]
        add_slider_row(0, "Shutter (us)", self.shutter_var, shutter_lo, shutter_hi, 100, "shutter_scale")
        add_slider_row(1, "Gain", self.gain_var, gain_lo, gain_hi, 0.1, "gain_scale")
        add_slider_row(2, "Lens position", self.lens_var, lens_lo, lens_hi, 0.05, "lens_scale")

        tk.Label(
            frame, text="AWB red gain", width=16, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
        ).grid(row=3, column=0, sticky="w", pady=4)
        awb_r_entry = tk.Entry(frame, textvariable=self.awb_r_var, width=8, justify="center")
        style_entry(awb_r_entry)
        awb_r_entry.grid(row=3, column=1, padx=6, sticky="w")
        awb_r_entry.bind("<Return>", self._apply_controls)
        awb_r_entry.bind("<FocusOut>", self._apply_controls)

        tk.Label(
            frame, text="AWB blue gain", width=16, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
        ).grid(row=4, column=0, sticky="w", pady=4)
        awb_b_entry = tk.Entry(frame, textvariable=self.awb_b_var, width=8, justify="center")
        style_entry(awb_b_entry)
        awb_b_entry.grid(row=4, column=1, padx=6, sticky="w")
        awb_b_entry.bind("<Return>", self._apply_controls)
        awb_b_entry.bind("<FocusOut>", self._apply_controls)

        self.control_widgets.extend([awb_r_entry, awb_b_entry])

    def _build_preview_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Live preview", padding=12)
        frame.pack(fill="both", expand=True)

        # A Label's width/height are character units unless it already holds an
        # image, so seed a black placeholder image up front to get pixel sizing
        # from the very first layout pass (avoids a giant text-sized box). Kept
        # around so the panel can be reset back to black whenever preview stops.
        self._black_placeholder = ImageTk.PhotoImage(Image.new("RGB", PREVIEW_SIZE, "black"))
        self._preview_photo = self._black_placeholder
        preview_border = tk.Frame(frame, bg=COLORS["header_bg"], padx=2, pady=2)
        preview_border.pack(pady=(0, 10))
        self.preview_label = tk.Label(preview_border, image=self._preview_photo, bg="black")
        self.preview_label.pack()

        # Snapshot thumbnails captured during the current/last recording.
        # Labels are created on demand per thumbnail (sidesteps the
        # width/height-is-character-units gotcha for empty Labels) and
        # wrap into a new row every THUMBS_PER_ROW.
        tk.Label(
            frame, text="Snapshots", bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONT_BASE,
        ).pack(anchor="w")
        self.snapshot_strip = tk.Frame(frame, bg=COLORS["surface"])
        self.snapshot_strip.pack(pady=(4, 10), fill="x")
        self._snapshot_thumb_labels = []
        self._snapshot_thumb_photos = []

        # Deliberately NOT added to control_widgets: live view should stay
        # toggleable during recording, not get greyed out.
        self.button_preview = tk.Button(frame, text="Preview", width=12, command=self.toggle_preview)
        style_button(self.button_preview, "primary")
        self.button_preview.pack(pady=5)

    def _clear_snapshot_thumbnails(self):
        for label in self._snapshot_thumb_labels:
            label.destroy()
        self._snapshot_thumb_labels = []
        self._snapshot_thumb_photos = []

    def _add_snapshot_thumbnail(self, pil_thumb, snap_index):
        photo = ImageTk.PhotoImage(pil_thumb)
        self._snapshot_thumb_photos.append(photo)  # keep a live ref, Tk won't
        row, col = divmod(len(self._snapshot_thumb_labels), THUMBS_PER_ROW)
        label = tk.Label(
            self.snapshot_strip, image=photo, bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        label.grid(row=row, column=col, padx=3, pady=3)
        self._snapshot_thumb_labels.append(label)

    def _build_action_frame(self, parent):
        card = tk.Frame(parent, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=COLORS["surface"], padx=15, pady=12)
        inner.pack(fill="x")

        tk.Label(inner, text="Duration (s)", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE).grid(
            row=0, column=0, padx=(0, 8)
        )
        duration_entry = tk.Entry(inner, textvariable=self.duration_var, width=8, justify="center")
        style_entry(duration_entry)
        duration_entry.grid(row=0, column=1, padx=(0, 20))

        tk.Label(inner, text="Snapshots", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE).grid(
            row=0, column=2, padx=(0, 8)
        )
        snaps_entry = tk.Entry(inner, textvariable=self.n_snaps_var, width=8, justify="center")
        style_entry(snaps_entry)
        snaps_entry.grid(row=0, column=3, padx=(0, 20))

        self.button_record = tk.Button(inner, text="Record", width=15, command=self.on_record_clicked)
        style_button(self.button_record, "primary")
        self.button_record.grid(row=0, column=4, padx=(0, 12))

        # Deliberately NOT added to control_widgets: that list gets disabled
        # during recording, but this button must stay live *only* during
        # recording (the opposite lifecycle), so its enabled state is
        # toggled directly in on_record_clicked / _record_finished.
        self.button_emergency_stop = tk.Button(
            inner, text="EMERGENCY STOP", width=16, state=tk.DISABLED, command=self._on_emergency_stop,
        )
        style_button(self.button_emergency_stop, "danger")
        self.button_emergency_stop.grid(row=0, column=5)

        self.control_widgets.extend([duration_entry, snaps_entry, self.button_record])

        tk.Label(
            inner, textvariable=self.status_var, bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(12, 0))

        self._last_output_folder = None
        self.folder_link_var = tk.StringVar(value="")
        self.label_folder_link = tk.Label(
            inner, textvariable=self.folder_link_var, bg=COLORS["surface"], fg=COLORS["primary"],
            cursor="hand2", font=(FONT_FAMILY, 10, "underline"),
        )
        self.label_folder_link.grid(row=2, column=0, columnspan=6, sticky="w", pady=(2, 0))
        self.label_folder_link.bind("<Button-1>", self._open_output_folder)

    def _open_output_folder(self, event=None):
        if not self._last_output_folder:
            return
        try:
            subprocess.Popen(["xdg-open", self._last_output_folder])
        except OSError as exc:
            messagebox.showerror(
                "Open folder", f"Could not open folder:\n{self._last_output_folder}\n\n{exc}"
            )

    def _build_settings_tab(self, parent):
        self._settings_form_vars = {}
        outer = ttk.Frame(parent, padding=15)
        outer.pack(fill="both", expand=True)

        def add_form_var(key, value):
            var = tk.StringVar(value=str(value))
            self._settings_form_vars[key] = var
            return var

        def col_header(container, text, col, anchor="w"):
            sticky = anchor if anchor in ("w", "e") else ""
            tk.Label(
                container, text=text, font=(FONT_FAMILY, 9, "bold"), bg=COLORS["surface"],
                fg=COLORS["text_muted"], anchor=anchor,
            ).grid(row=0, column=col, sticky=sticky, pady=(0, 6))

        def row_label(container, text, row):
            tk.Label(
                container, text=text, width=16, anchor="w", bg=COLORS["surface"], fg=COLORS["text"],
                font=FONT_BASE,
            ).grid(row=row, column=0, sticky="w", pady=4)

        def make_entry(container, var, row, col, width=10, sticky=None, padx=6):
            entry = tk.Entry(container, textvariable=var, width=width, justify="center")
            style_entry(entry)
            entry.grid(row=row, column=col, padx=padx, sticky=sticky)
            return entry

        preset_frame = ttk.LabelFrame(outer, text="Test condition presets & defaults", padding=12)
        preset_frame.pack(fill="x", pady=(0, 12))
        col_header(preset_frame, "Field", 0)
        col_header(preset_frame, "Selectable values (comma-separated)", 1)
        col_header(preset_frame, "Default", 2)

        preset_fields = [
            ("blend_gas", "Blending gas"),
            ("blend_ratio", "Blending ratio (%)"),
            ("phi", "Phi"),
            ("lance_depth", "Lance depth (mm)"),
            ("lance_size", "Lance size (mm)"),
        ]
        for row, (key, label) in enumerate(preset_fields, start=1):
            row_label(preset_frame, label, row)
            values_var = add_form_var(f"{key}_values", ", ".join(str(v) for v in self.presets[key]))
            make_entry(preset_frame, values_var, row, 1, width=44, sticky="w")
            default_var = add_form_var(f"{key}_default", self.defaults[key])
            make_entry(preset_frame, default_var, row, 2)

        range_frame = ttk.LabelFrame(outer, text="Camera control ranges & defaults", padding=12)
        range_frame.pack(fill="x", pady=(0, 12))
        col_header(range_frame, "Field", 0)
        col_header(range_frame, "Min", 1, anchor="center")
        col_header(range_frame, "Max", 2, anchor="center")
        col_header(range_frame, "Default", 3, anchor="center")

        range_fields = [
            ("shutter", "Shutter (us)"),
            ("gain", "Gain"),
            ("lens", "Lens position"),
        ]
        for row, (key, label) in enumerate(range_fields, start=1):
            row_label(range_frame, label, row)
            lo, hi = self.ranges[key]
            min_var = add_form_var(f"{key}_min", lo)
            make_entry(range_frame, min_var, row, 1)
            max_var = add_form_var(f"{key}_max", hi)
            make_entry(range_frame, max_var, row, 2)
            default_var = add_form_var(f"{key}_default", self.defaults[key])
            make_entry(range_frame, default_var, row, 3)

        other_frame = ttk.LabelFrame(outer, text="Other defaults", padding=12)
        other_frame.pack(fill="x", pady=(0, 12))
        other_fields = [
            ("kw", "kW default"),
            ("awb_r", "AWB red gain default"),
            ("awb_b", "AWB blue gain default"),
            ("duration", "Duration (s) default"),
            ("n_snaps", "Snapshots default"),
        ]
        for i, (key, label) in enumerate(other_fields):
            row, col = divmod(i, 3)
            tk.Label(
                other_frame, text=label, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE,
            ).grid(row=row, column=col * 2, sticky="w", padx=(0 if col == 0 else 15, 6), pady=4)
            var = add_form_var(f"{key}_default", self.defaults[key])
            make_entry(other_frame, var, row, col * 2 + 1)

        button_row = ttk.Frame(outer)
        button_row.pack(pady=(4, 10))
        self.button_settings_apply = tk.Button(
            button_row, text="Save & Apply", width=14, command=self._on_settings_apply,
        )
        style_button(self.button_settings_apply, "primary")
        self.button_settings_apply.pack(side="left", padx=5)
        self.button_settings_restore = tk.Button(
            button_row, text="Restore Factory Defaults", width=22, command=self._on_settings_restore_factory,
        )
        style_button(self.button_settings_restore, "secondary")
        self.button_settings_restore.pack(side="left", padx=5)
        # Disabled during recording along with everything else in
        # control_widgets, so a mid-recording preset/range change can't
        # race the camera thread reading self.presets/self.ranges.
        self.control_widgets.extend([self.button_settings_apply, self.button_settings_restore])

        self.settings_status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.settings_status_var).pack(pady=(0, 0))

    def _refresh_settings_form(self):
        for key in ("blend_gas", "blend_ratio", "phi", "lance_depth", "lance_size"):
            self._settings_form_vars[f"{key}_values"].set(", ".join(str(v) for v in self.presets[key]))
            self._settings_form_vars[f"{key}_default"].set(str(self.defaults[key]))
        for key in ("shutter", "gain", "lens"):
            lo, hi = self.ranges[key]
            self._settings_form_vars[f"{key}_min"].set(str(lo))
            self._settings_form_vars[f"{key}_max"].set(str(hi))
            self._settings_form_vars[f"{key}_default"].set(str(self.defaults[key]))
        for key in ("kw", "awb_r", "awb_b", "duration", "n_snaps"):
            self._settings_form_vars[f"{key}_default"].set(str(self.defaults[key]))

    def _reconfigure_ranges(self):
        # Reconfigure from_/to before touching the vars below, so a var
        # isn't transiently clamped against a stale slider range.
        lo, hi = self.ranges["shutter"]
        self.shutter_scale.config(from_=lo, to=hi)
        lo, hi = self.ranges["gain"]
        self.gain_scale.config(from_=lo, to=hi)
        lo, hi = self.ranges["lens"]
        self.lens_scale.config(from_=lo, to=hi)

    def _apply_defaults_to_vars(self):
        self.blend_gas_var.set(str(self.defaults["blend_gas"]))
        self.blend_var.set(str(self.defaults["blend_ratio"]))
        self.phi_var.set(str(self.defaults["phi"]))
        self.lance_depth_var.set(str(self.defaults["lance_depth"]))
        self.lance_size_var.set(str(self.defaults["lance_size"]))
        self.kw_var.set(str(self.defaults["kw"]))
        self.shutter_var.set(self.defaults["shutter"])
        self.gain_var.set(self.defaults["gain"])
        self.awb_r_var.set(self.defaults["awb_r"])
        self.awb_b_var.set(self.defaults["awb_b"])
        self.lens_var.set(self.defaults["lens"])
        self.duration_var.set(_fmt_num(self.defaults["duration"]))
        self.n_snaps_var.set(str(self.defaults["n_snaps"]))
        if self.camera_ready:
            self._apply_controls()

    def _on_settings_apply(self):
        if self._recording_in_progress:
            messagebox.showerror("Busy", "Cannot change settings while a recording is in progress.")
            return
        raw = {key: var.get() for key, var in self._settings_form_vars.items()}
        try:
            presets, defaults, ranges = parse_settings_form(raw)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        self.presets, self.defaults, self.ranges = presets, defaults, ranges
        try:
            save_settings(presets, defaults, ranges)
            self.settings_status_var.set("Settings saved and applied.")
        except OSError as exc:
            self.settings_status_var.set("Settings applied for this session only (save to disk failed).")
            messagebox.showwarning("Settings", f"Applied for this session, but failed to save to disk:\n{exc}")

        self._reconfigure_ranges()
        self._apply_defaults_to_vars()

    def _on_settings_restore_factory(self):
        if self._recording_in_progress:
            messagebox.showerror("Busy", "Cannot change settings while a recording is in progress.")
            return
        if not messagebox.askyesno(
            "Restore factory defaults", "Discard current settings and restore factory defaults?"
        ):
            return

        self.presets = {k: list(v) for k, v in FACTORY_PRESETS.items()}
        self.defaults = dict(FACTORY_DEFAULTS)
        self.ranges = {k: list(v) for k, v in FACTORY_RANGES.items()}
        try:
            save_settings(self.presets, self.defaults, self.ranges)
            self.settings_status_var.set("Factory defaults restored.")
        except OSError as exc:
            self.settings_status_var.set("Factory defaults restored for this session only (save to disk failed).")
            messagebox.showwarning("Settings", f"Restored for this session, but failed to save to disk:\n{exc}")

        self._reconfigure_ranges()
        self._apply_defaults_to_vars()
        self._refresh_settings_form()

    # ---------------------------------------------------------------- #
    # Thread-safe GUI update marshalling
    # ---------------------------------------------------------------- #
    def _poll_gui_queue(self):
        # The reschedule below must always run, even if a callback raises or
        # the queue drain hits an unexpected error - otherwise this loop dies
        # silently and the GUI never processes another status/finished update
        # (looks like the app "never returns to normal" after recording).
        try:
            while True:
                try:
                    callback, args = self._gui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback(*args)
                except Exception as exc:
                    print(f"GUI queue callback error: {exc}")
        finally:
            self.root.after(50, self._poll_gui_queue)

    # ---------------------------------------------------------------- #
    # Generalized picklist popup
    # ---------------------------------------------------------------- #
    def show_picklist(self, title, values, target_var):
        current = target_var.get()
        local_choice = tk.StringVar(value=current if current in [str(v) for v in values] else str(values[0]))

        def select():
            target_var.set(local_choice.get())
            sub_window.destroy()

        sub_window = tk.Toplevel(self.root, bg=COLORS["surface"])
        sub_window.title(title)
        sub_window.transient(self.root)
        sub_window.resizable(False, False)
        sub_window.grab_set()

        tk.Label(
            sub_window, text=title, bg=COLORS["surface"], fg=COLORS["text"], font=FONT_SECTION, pady=12,
        ).pack(padx=20)
        for v in values:
            tk.Radiobutton(
                sub_window, text=str(v), value=str(v), variable=local_choice,
                bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE, selectcolor=COLORS["primary_light"],
                activebackground=COLORS["surface"], anchor="w", padx=20,
            ).pack(fill="x", padx=20)

        button = tk.Button(sub_window, text="Select", width=10, command=select)
        style_button(button, "primary")
        button.pack(pady=16)

    # ---------------------------------------------------------------- #
    # Camera lifecycle
    # ---------------------------------------------------------------- #
    def init_camera(self):
        # This is the one and only time picam2 is touched from the main
        # thread: setup happens here, before the persistent camera thread
        # exists, so there's no other thread it could race against yet.
        try:
            self.picam2 = Picamera2()
            config = self.picam2.create_video_configuration(
                main={"size": MAIN_SIZE, "format": "RGB888"},
                lores={"size": PREVIEW_SIZE, "format": "RGB888"},
                display="lores",
                transform=Transform(hflip=1, vflip=1),
                controls={"FrameRate": 30},
            )
            self.picam2.configure(config)
            self.picam2.start()
            self.camera_ready = True
            self._camera_thread = threading.Thread(target=self._camera_thread_fn, daemon=True)
            self._camera_thread.start()
            self._apply_controls()
        except Exception as exc:
            self.camera_ready = False
            messagebox.showerror("Camera error", f"Failed to initialize camera:\n{exc}")
            self.button_preview.config(state=tk.DISABLED)
            self.button_record.config(state=tk.DISABLED)

    def on_close(self):
        if self._recording_in_progress:
            if not messagebox.askokcancel(
                "Recording in progress", "A recording is still running. Quit anyway?"
            ):
                return
            self._abort_recording = True

        self.preview_active = False
        self._shutdown_event.set()
        if self._camera_thread is not None:
            self._camera_thread.join(timeout=5)

        if self.picam2 is not None:
            try:
                self.picam2.close()
            except Exception:
                pass

        self.root.destroy()

    # ---------------------------------------------------------------- #
    # Live preview (UI side - just flags/widgets, no picam2 calls; the
    # persistent camera thread is what actually captures frames)
    # ---------------------------------------------------------------- #
    def toggle_preview(self):
        if not self.camera_ready:
            return
        self.preview_active = not self.preview_active
        self._preview_epoch += 1
        if self.preview_active:
            self.button_preview.config(text="Stop Preview")
        else:
            self.button_preview.config(text="Preview")
            self._preview_photo = self._black_placeholder
            self.preview_label.configure(image=self._preview_photo)

    def _render_preview_frame(self, frame, epoch):
        if epoch != self._preview_epoch:
            return  # stale frame captured before the most recent start/stop
        img = Image.fromarray(frame)
        self._preview_photo = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=self._preview_photo)

    # ---------------------------------------------------------------- #
    # Live controls
    # ---------------------------------------------------------------- #
    def _current_controls_dict(self):
        return {
            "ExposureTime": int(self.shutter_var.get()),
            "AnalogueGain": float(self.gain_var.get()),
            "ColourGains": (float(self.awb_r_var.get()), float(self.awb_b_var.get())),
            "LensPosition": float(self.lens_var.get()),
        }

    def _apply_controls(self, *_):
        if not self.camera_ready:
            return
        try:
            controls = self._current_controls_dict()
        except (tk.TclError, ValueError) as exc:
            self.status_var.set(f"Control update failed: {exc}")
            return
        self._camera_cmd_queue.put(("apply_controls", controls))

    # ---------------------------------------------------------------- #
    # Persistent camera thread - the sole owner of self.picam2 for the
    # app's entire lifetime. Processes queued commands (control updates,
    # record requests) and otherwise grabs idle-preview frames, pushing
    # everything destined for the GUI through _gui_queue.
    # ---------------------------------------------------------------- #
    def _camera_thread_fn(self):
        while not self._shutdown_event.is_set():
            try:
                cmd = self._camera_cmd_queue.get(timeout=0.05)
            except queue.Empty:
                cmd = None

            if cmd is not None:
                kind = cmd[0]
                if kind == "apply_controls":
                    try:
                        self.picam2.set_controls(cmd[1])
                    except Exception as exc:
                        self._gui_queue.put((self._set_status, (f"Control update failed: {exc}",)))
                elif kind == "record":
                    _, output_folder, duration_s, n_snaps = cmd
                    self._do_record(output_folder, duration_s, n_snaps)
                continue

            if self.preview_active and not self._recording_in_progress:
                epoch = self._preview_epoch
                try:
                    frame = self.picam2.capture_array("lores")
                    self._gui_queue.put((self._render_preview_frame, (frame, epoch)))
                except Exception:
                    pass

    # ---------------------------------------------------------------- #
    # Folder naming
    # ---------------------------------------------------------------- #
    def build_output_folder(self):
        date_str = datetime.now().strftime("%Y%m%d")
        base_name = (
            f"{self.blend_gas_var.get()}blend{self.blend_var.get()}pc_"
            f"lanceD{self.lance_depth_var.get()}mm_"
            f"lanceS{self.lance_size_var.get()}mm_"
            f"{self.kw_var.get()}kW_"
            f"phi{self.phi_var.get()}"
        )
        day_dir = os.path.expanduser(f"~/Desktop/{date_str}")
        os.makedirs(day_dir, exist_ok=True)

        candidate = os.path.join(day_dir, base_name)
        if not os.path.exists(candidate):
            os.makedirs(candidate)
            return candidate

        choice = self._ask_overwrite_or_new_run(candidate)
        if choice == "cancel":
            return None
        if choice == "overwrite":
            for name in os.listdir(candidate):
                if name == "video.mp4" or (name.startswith("snap") and name.endswith(".jpg")):
                    try:
                        os.remove(os.path.join(candidate, name))
                    except OSError:
                        pass
            return candidate

        max_run = 1
        for item in os.listdir(day_dir):
            if item.startswith(base_name + "_run"):
                suffix = item[len(base_name) + 4:]
                if suffix.isdigit():
                    max_run = max(max_run, int(suffix))

        new_folder = os.path.join(day_dir, f"{base_name}_run{max_run + 1}")
        os.makedirs(new_folder)
        return new_folder

    def _ask_overwrite_or_new_run(self, candidate):
        """Modal 3-way prompt shown when today's folder for this exact
        condition already exists. Blocks (via wait_window) until the user
        picks one, since build_output_folder needs the answer synchronously.
        Returns "overwrite", "new_run", or "cancel".
        """
        result = {"choice": "cancel"}

        def pick(choice):
            result["choice"] = choice
            dialog.destroy()

        dialog = tk.Toplevel(self.root, bg=COLORS["surface"])
        dialog.title("Folder already exists")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: pick("cancel"))

        tk.Label(
            dialog, bg=COLORS["surface"], fg=COLORS["text"], font=FONT_BASE, pady=14, justify="left",
            text=(
                f"A recording already exists for this exact condition today:\n\n"
                f"{os.path.basename(candidate)}\n\n"
                "Overwrite it, or save this run separately?"
            ),
        ).pack(padx=20)

        button_row = tk.Frame(dialog, bg=COLORS["surface"])
        button_row.pack(pady=14)
        btn_overwrite = tk.Button(button_row, text="Overwrite", width=12, command=lambda: pick("overwrite"))
        style_button(btn_overwrite, "danger")
        btn_overwrite.pack(side="left", padx=5)
        btn_new_run = tk.Button(button_row, text="New Run", width=12, command=lambda: pick("new_run"))
        style_button(btn_new_run, "primary")
        btn_new_run.pack(side="left", padx=5)
        btn_cancel = tk.Button(button_row, text="Cancel", width=12, command=lambda: pick("cancel"))
        style_button(btn_cancel, "secondary")
        btn_cancel.pack(side="left", padx=5)

        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["choice"]

    # ---------------------------------------------------------------- #
    # Validation
    # ---------------------------------------------------------------- #
    def _validate_all(self):
        if self.kw_var.get().strip() == "":
            messagebox.showinfo("Data not found", "Please enter the kW value.")
            return False
        try:
            float(self.kw_var.get())
        except ValueError:
            messagebox.showerror("Invalid value", "kW must be a number.")
            return False

        try:
            duration = float(self.duration_var.get())
            if duration <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value", "Duration must be a positive number.")
            return False

        try:
            n_snaps = int(self.n_snaps_var.get())
            if n_snaps < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value", "Snapshot count must be a non-negative integer.")
            return False

        return True

    def _set_controls_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.control_widgets:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass

    # ---------------------------------------------------------------- #
    # Record workflow
    # ---------------------------------------------------------------- #
    def on_record_clicked(self):
        if not self.camera_ready or self._recording_in_progress:
            return
        if not self._validate_all():
            return

        try:
            output_folder = self.build_output_folder()
        except OSError as exc:
            messagebox.showerror("Folder error", str(exc))
            return
        if output_folder is None:
            # User cancelled the overwrite/new-run prompt - nothing to do.
            return

        duration_s = float(self.duration_var.get())
        n_snaps = int(self.n_snaps_var.get())

        self._recording_in_progress = True
        self._abort_recording = False
        self._set_controls_enabled(False)
        self.button_emergency_stop.config(state=tk.NORMAL)
        self.status_var.set(f"Recording to {output_folder} ...")
        self._clear_snapshot_thumbnails()
        self.folder_link_var.set("")

        # Live view should stay visible through the whole recording, so make
        # sure it's actually running even if the user never clicked Preview.
        if not self.preview_active:
            self.toggle_preview()

        self._camera_cmd_queue.put(("record", output_folder, duration_s, n_snaps))

    def _on_emergency_stop(self):
        # Aborts the currently-running recording loop (checked every ~10ms
        # in _do_record, so this is responsive). Not a recovery mechanism
        # for a wedged camera call - just an early-stop for a normal run.
        if not self._recording_in_progress:
            return
        self._abort_recording = True
        self.status_var.set("Stopping recording...")
        self.button_emergency_stop.config(state=tk.DISABLED)

    def _do_record(self, output_folder, duration_s, n_snaps):
        # Runs on the persistent camera thread (see _camera_thread_fn) - this
        # is what grabs live-preview frames during recording too, rather than
        # any other thread calling capture_array() concurrently or shortly
        # after stop_recording(), which hangs forever on this hardware.
        error = None
        recording_started = False
        try:
            encoder = H264Encoder()
            video_path = os.path.join(output_folder, "video.mp4")
            self.picam2.start_recording(encoder, FfmpegOutput(video_path))
            recording_started = True

            start_time = time.monotonic()
            end_time = start_time + duration_s
            snap_interval = duration_s / (n_snaps + 1) if n_snaps > 0 else None
            snap_times = (
                [start_time + i * snap_interval for i in range(1, n_snaps + 1)]
                if snap_interval else []
            )
            snap_index = 0
            preview_period = PREVIEW_INTERVAL_MS / 1000.0
            last_preview_t = 0.0
            last_status_t = 0.0

            while True:
                now = time.monotonic()
                if now >= end_time or self._abort_recording:
                    break

                if snap_index < len(snap_times) and now >= snap_times[snap_index]:
                    snap_path = os.path.join(output_folder, f"snap{snap_index + 1:02d}.jpg")
                    self.picam2.capture_file(snap_path, name="main")
                    snap_index += 1
                    self._gui_queue.put((
                        self._set_status,
                        (f"Captured {os.path.basename(snap_path)} ({snap_index}/{n_snaps})",),
                    ))
                    # Pure-PIL resize here (no Tk calls off the main thread,
                    # same discipline as the preview frames) - the callback
                    # only creates the PhotoImage, on the main thread.
                    try:
                        thumb = Image.open(snap_path)
                        thumb.thumbnail(THUMB_SIZE)
                        thumb = thumb.convert("RGB")
                        self._gui_queue.put((self._add_snapshot_thumbnail, (thumb, snap_index)))
                    except Exception:
                        pass
                elif self.preview_active and now - last_preview_t >= preview_period:
                    epoch = self._preview_epoch
                    try:
                        frame = self.picam2.capture_array("lores")
                        self._gui_queue.put((self._render_preview_frame, (frame, epoch)))
                    except Exception:
                        pass
                    last_preview_t = now
                else:
                    time.sleep(0.01)

                now = time.monotonic()
                if now - last_status_t >= 1.0:
                    remaining = max(0.0, end_time - now)
                    self._gui_queue.put((
                        self._set_status,
                        (f"Recording... {duration_s - remaining:.0f}/{duration_s:.0f}s",),
                    ))
                    last_status_t = now
        except Exception as exc:
            error = exc
        finally:
            # stop_recording() stops the camera pipeline outright (it's
            # stop_encoder() + stop()) - it does NOT leave the camera
            # streaming. Without an explicit restart here, the very next
            # idle-preview capture_array() call in _camera_thread_fn blocks
            # forever waiting for a frame from a camera that isn't running.
            # This, not any cross-thread timing issue, was the actual cause
            # of the app freezing on a second Record. Runtime controls can
            # revert across the stop/start cycle, so reapply them too.
            if recording_started:
                try:
                    self.picam2.stop_recording()
                except Exception:
                    pass
                try:
                    self.picam2.start()
                    self.picam2.set_controls(self._current_controls_dict())
                except Exception:
                    pass

        self._gui_queue.put((self._record_finished, (output_folder, error)))

    def _set_status(self, text):
        self.status_var.set(text)

    def _record_finished(self, output_folder, error):
        self._recording_in_progress = False
        self._set_controls_enabled(True)
        self.button_emergency_stop.config(state=tk.DISABLED)
        if error:
            messagebox.showerror("Recording failed", str(error))
            self.status_var.set(f"Recording failed: {error}")
        else:
            self.status_var.set("Recording complete.")
            self._last_output_folder = output_folder
            self.folder_link_var.set(f"Open folder: {os.path.basename(output_folder)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = CameraTestGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
