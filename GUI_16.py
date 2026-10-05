import json
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from datetime import datetime

from PIL import Image, ImageDraw, ImageTk
from libcamera import Transform, controls
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

PREVIEW_SIZE = (640, 360)
MAIN_SIZE = (1920, 1080)
PREVIEW_INTERVAL_MS = 66  # ~15 fps
THUMB_SIZE = (96, 54)
THUMBS_PER_ROW = 6

# Factory defaults and presets
FACTORY_PRESETS = {
    "blend_gas": ["None", "H2", "NH3"],
    "blend_ratio": [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    "phi": [0.85, 0.9, 0.95],
    "lance_depth": [10, 50, 100, 150, 200],
    "lance_size": [4, 6, 8],
}
FACTORY_DEFAULTS = {
    "blend_gas": "None",
    "blend_ratio": 0,
    "phi": 0.85,
    "lance_depth": 10,
    "lance_size": 4,
    "kw": "100",
    "shutter": 10000,
    "gain": 10.0,
    "awb_r": 2.0,
    "awb_b": 2.3,
    "focus_cm": 80.0,  # 80 cm = 1.25 dioptres (100 / 80)
    "duration": 10,
    "n_snaps": 5,
    "swap_rb": False,
}
FACTORY_RANGES = {
    "shutter": [100, 32000],
    "gain": [1.0, 16.0],
    "focus_cm": [4.0, 300.0],  # 4 cm (~25 D) to 300 cm (~0.33 D)
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui12_settings.json")

# Theme colors
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
    "danger_light": "#fee2e2",
    "success": "#16a34a",
    "success_light": "#dcfce7",
    "header_bg": "#1f2b45",
}


def style_button(button, kind="secondary", font_spec=None):
    btn_styles = {
        "primary": dict(bg=COLORS["primary"], fg="white", activebackground=COLORS["primary_dark"],
                         activeforeground="white", disabledforeground="#c7d5fa"),
        "danger": dict(bg=COLORS["danger"], fg="white", activebackground=COLORS["danger_dark"],
                        activeforeground="white", disabledforeground="#f3c2c2"),
        "secondary": dict(bg=COLORS["surface"], fg=COLORS["text"], activebackground=COLORS["primary_light"],
                           activeforeground=COLORS["primary"], disabledforeground=COLORS["text_muted"]),
        "success": dict(bg=COLORS["success"], fg="white", activebackground="#15803d",
                        activeforeground="white", disabledforeground="#bbf7d0"),
    }
    kwargs = btn_styles.get(kind, btn_styles["secondary"]).copy()
    if font_spec:
        kwargs["font"] = font_spec
    button.configure(
        relief="flat", bd=0, cursor="hand2", padx=12, pady=5,
        highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["border"],
        **kwargs,
    )


def style_entry(entry, font_spec=None):
    kwargs = dict(
        relief="flat", bd=1, highlightthickness=1,
        highlightbackground=COLORS["border"], highlightcolor=COLORS["primary"],
        bg="white", fg=COLORS["text"],
        disabledbackground=COLORS["bg"], disabledforeground=COLORS["text_muted"],
        readonlybackground=COLORS["primary_light"],
    )
    if font_spec:
        kwargs["font"] = font_spec
    entry.configure(**kwargs)


def _fmt_num(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def dist_cm_to_dioptres(dist_cm):
    """Convert focus distance in centimeters to libcamera LensPosition (dioptres = 1/m)."""
    try:
        dist = float(dist_cm)
    except (ValueError, TypeError):
        return 0.0
    if dist <= 0 or dist >= 500.0:
        return 0.0  # infinity focus
    # 100 cm = 1 meter -> dioptre = 100 / dist_cm
    return max(0.0, min(32.0, round(100.0 / dist, 3)))


def dioptres_to_dist_cm(dioptres):
    """Convert libcamera LensPosition (dioptres) to focus distance in centimeters."""
    try:
        d = float(dioptres)
    except (ValueError, TypeError):
        return 80.0
    if d <= 0.001:
        return 300.0  # Far/infinity
    return round(100.0 / d, 1)


def calculate_combustion_flow(kw_val, blend_gas, blend_ratio_val, phi_val):
    """
    Calculate fuel flow, air flow, and flue gas composition (Dry & Wet %).
    
    NG1 (Alicat Nat Gas 1) Volumetric Composition:
      CH4: 93%, C2H6: 3%, C3H8: 1%, N2: 2%, CO2: 1%
      
    Conditions: Normal conditions (0 °C, 101.325 kPa = 1 atm)
      Molar volume Vm = 0.022414 m3/mol = 22.414 Nm3/kmol
      
    LHV (Lower Heating Values):
      CH4:  802.3 kJ/mol -> 35.794 MJ/Nm3
      C2H6: 1428.6 kJ/mol -> 63.737 MJ/Nm3
      C3H8: 2043.1 kJ/mol -> 91.153 MJ/Nm3
      NG1:  36.113 MJ/Nm3
      H2:   241.82 kJ/mol -> 10.789 MJ/Nm3
      NH3:  316.7 kJ/mol  -> 14.129 MJ/Nm3
    """
    try:
        kw = float(kw_val)
        if kw <= 0:
            return {"error": "kW must be > 0"}
    except (ValueError, TypeError):
        return {"error": "Enter valid kW"}

    try:
        r_pct = float(blend_ratio_val)
        r_v = max(0.0, min(100.0, r_pct)) / 100.0
    except (ValueError, TypeError):
        r_v = 0.0

    try:
        phi = float(phi_val)
        if phi <= 0:
            return {"error": "Phi must be > 0"}
    except (ValueError, TypeError):
        return {"error": "Enter valid Phi"}

    Vm = 0.022414
    lhv_ch4 = 802.3 / Vm / 1000.0
    lhv_c2h6 = 1428.6 / Vm / 1000.0
    lhv_c3h8 = 2043.1 / Vm / 1000.0
    lhv_ng1 = 0.93 * lhv_ch4 + 0.03 * lhv_c2h6 + 0.01 * lhv_c3h8

    lhv_h2 = 241.82 / Vm / 1000.0
    lhv_nh3 = 316.7 / Vm / 1000.0

    nu_o2_ng1 = 0.93 * 2.0 + 0.03 * 3.5 + 0.01 * 5.0  # 2.015
    co2_per_ng1 = 0.93 * 1.0 + 0.03 * 2.0 + 0.01 * 3.0 + 0.01 * 1.0  # 1.03
    h2o_per_ng1 = 0.93 * 2.0 + 0.03 * 3.0 + 0.01 * 4.0  # 1.99
    n2_per_ng1 = 0.02

    bg = str(blend_gas).strip().upper()
    if bg in ("NONE", "", "PURE NG", "PURE_NG") or r_v == 0.0:
        nu_o2_blend = 0.0
        co2_per_blend = 0.0
        h2o_per_blend = 0.0
        n2_per_blend = 0.0
        lhv_mix = lhv_ng1
        r_v = 0.0
    elif bg == "H2":
        nu_o2_blend = 0.5
        co2_per_blend = 0.0
        h2o_per_blend = 1.0
        n2_per_blend = 0.0
        lhv_mix = (1.0 - r_v) * lhv_ng1 + r_v * lhv_h2
    elif bg == "NH3":
        nu_o2_blend = 0.75  # NH3 + 0.75 O2 -> 0.5 N2 + 1.5 H2O
        co2_per_blend = 0.0
        h2o_per_blend = 1.5
        n2_per_blend = 0.5
        lhv_mix = (1.0 - r_v) * lhv_ng1 + r_v * lhv_nh3
    else:
        return {"error": f"Unknown gas {blend_gas}"}

    e_dot = kw * 3.6  # MJ/h
    v_dot_fuel = e_dot / lhv_mix

    v_dot_ng = (1.0 - r_v) * v_dot_fuel
    v_dot_blend = r_v * v_dot_fuel

    v_dot_o2_stoich = v_dot_ng * nu_o2_ng1 + v_dot_blend * nu_o2_blend
    v_dot_air = v_dot_o2_stoich / (0.21 * phi)

    v_dot_co2 = v_dot_ng * co2_per_ng1 + v_dot_blend * co2_per_blend
    v_dot_h2o = v_dot_ng * h2o_per_ng1 + v_dot_blend * h2o_per_blend
    o2_in = 0.21 * v_dot_air
    v_dot_o2 = max(0.0, o2_in - v_dot_o2_stoich)
    v_dot_n2 = 0.79 * v_dot_air + v_dot_ng * n2_per_ng1 + v_dot_blend * n2_per_blend

    v_dot_flue_dry = v_dot_co2 + v_dot_o2 + v_dot_n2
    v_dot_flue_wet = v_dot_flue_dry + v_dot_h2o

    o2_pct_dry = (v_dot_o2 / v_dot_flue_dry * 100.0) if v_dot_flue_dry > 0 else 0.0
    co2_pct_dry = (v_dot_co2 / v_dot_flue_dry * 100.0) if v_dot_flue_dry > 0 else 0.0

    o2_pct_wet = (v_dot_o2 / v_dot_flue_wet * 100.0) if v_dot_flue_wet > 0 else 0.0
    co2_pct_wet = (v_dot_co2 / v_dot_flue_wet * 100.0) if v_dot_flue_wet > 0 else 0.0

    return {
        "error": None,
        "v_dot_ng": v_dot_ng,
        "v_dot_blend": v_dot_blend,
        "v_dot_air": v_dot_air,
        "o2_pct_dry": o2_pct_dry,
        "co2_pct_dry": co2_pct_dry,
        "o2_pct_wet": o2_pct_wet,
        "co2_pct_wet": co2_pct_wet,
    }


def parse_settings_form(raw):
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

    for key, label in (
        ("blend_gas", "Blending gas"),
        ("blend_ratio", "Blending ratio"),
        ("phi", "Phi"),
        ("lance_depth", "Lance depth"),
        ("lance_size", "Lance size"),
    ):
        caster = str if key == "blend_gas" else (float if key == "phi" else int)
        values = parse_list(raw[f"{key}_values"], caster, f"{label} presets")
        default_ = parse_num(raw[f"{key}_default"], caster, f"{label} default")
        if default_ not in values:
            raise ValueError(f"{label} default: '{default_}' must be one of the selectable values ({values})")
        presets[key] = values
        defaults[key] = default_

    for key, label, caster in (
        ("shutter", "Shutter (us)", int),
        ("gain", "Gain", float),
        ("focus_cm", "Focus distance (cm)", float),
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
            raise ValueError(f"kW default: '{defaults['kw']}' must be a number or empty")

    for key, label, caster in (
        ("awb_r", "AWB red gain", float),
        ("awb_b", "AWB blue gain", float),
        ("duration", "Duration (s)", float),
        ("n_snaps", "Snapshot count", int),
    ):
        val = parse_num(raw[f"{key}_default"], caster, f"{label} default")
        if val <= 0 and key in ("awb_r", "awb_b", "duration"):
            raise ValueError(f"{label}: must be greater than zero")
        if val < 0 and key == "n_snaps":
            raise ValueError(f"{label}: must be non-negative")
        defaults[key] = val

    return presets, defaults, ranges


def load_settings():
    presets = {k: list(v) for k, v in FACTORY_PRESETS.items()}
    defaults = dict(FACTORY_DEFAULTS)
    ranges = {k: list(v) for k, v in FACTORY_RANGES.items()}
    warnings = []

    if not os.path.exists(CONFIG_PATH):
        try:
            save_settings(presets, defaults, ranges)
        except OSError as exc:
            warnings.append(f"Could not write default config to {CONFIG_PATH}: {exc}")
        return presets, defaults, ranges, warnings

    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(f"Failed to read {CONFIG_PATH} ({exc}); using factory defaults.")
        return presets, defaults, ranges, warnings

    loaded_presets = data.get("presets", {})
    loaded_defaults = data.get("defaults", {})
    loaded_ranges = data.get("ranges", {})

    for key in FACTORY_PRESETS:
        if key not in loaded_presets:
            continue
        try:
            caster = str if key == "blend_gas" else (float if key == "phi" else int)
            vals = [caster(x) for x in loaded_presets[key]]
            if not vals:
                raise ValueError("empty list")
            default_ = caster(loaded_defaults.get(key, FACTORY_DEFAULTS[key]))
            if default_ not in vals:
                default_ = vals[0]
                warnings.append(f"Reset {key} default to '{default_}' (previous not in presets).")
            presets[key] = vals
            defaults[key] = default_
        except (ValueError, TypeError) as exc:
            warnings.append(f"Ignoring saved '{key}' preset ({exc}); using factory default.")

    # Ensure "None" is in blend_gas presets
    if "blend_gas" in presets and "None" not in presets["blend_gas"]:
        presets["blend_gas"].insert(0, "None")

    # Backward compatibility: convert legacy "lens" (dioptres) to "focus_cm" (cm)
    if "focus_cm" not in loaded_defaults and "lens" in loaded_defaults:
        loaded_defaults["focus_cm"] = dioptres_to_dist_cm(loaded_defaults["lens"])
    if "focus_cm" not in loaded_ranges and "lens" in loaded_ranges:
        loaded_ranges["focus_cm"] = [4.0, 300.0]

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

    for key in ("kw", "awb_r", "awb_b", "duration", "n_snaps", "swap_rb"):
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
        self.root.title("ReactingFlow Lab - Camera & Flow Controller")
        self.root.geometry("1280x920")

        # Native font caching
        try:
            self.font_family = tkfont.nametofont("TkDefaultFont").cget("family")
        except Exception:
            self.font_family = "DejaVu Sans"

        self.font_base = (self.font_family, 10)
        self.font_header = (self.font_family, 16, "bold")
        self.font_subheader = (self.font_family, 11)
        self.font_section = (self.font_family, 10, "bold")
        self.font_button = (self.font_family, 10, "bold")
        self.font_metric_lbl = (self.font_family, 9, "bold")
        self.font_metric_val = ("DejaVu Sans Mono", 9, "bold")

        self.presets, self.defaults, self.ranges, load_warnings = load_settings()

        self._slider_debounce_id = None
        self._preview_timer_id = None
        self.swap_rb_var = tk.BooleanVar(value=bool(self.defaults.get("swap_rb", False)))
        self.swap_rb = self.swap_rb_var.get()

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

        # Focus Distance in Centimeters (1/f)
        self.focus_cm_var = tk.DoubleVar(value=float(self.defaults.get("focus_cm", 80.0)))

        self.duration_var = tk.StringVar(value=_fmt_num(self.defaults["duration"]))
        self.n_snaps_var = tk.StringVar(value=str(self.defaults["n_snaps"]))
        self.status_var = tk.StringVar(value="")

        # Flow calculation output variables
        self.calc_ng_var = tk.StringVar(value="—")
        self.calc_blend_lbl_var = tk.StringVar(value="Blend gas flow:")
        self.calc_blend_val_var = tk.StringVar(value="None (Pure NG)")
        self.calc_air_lbl_var = tk.StringVar(value="Air flow (actual):")
        self.calc_air_val_var = tk.StringVar(value="—")
        self.calc_o2_var = tk.StringVar(value="—")
        self.calc_co2_var = tk.StringVar(value="—")

        self.picam2 = None
        self.camera_ready = False
        self.preview_active = False
        self._preview_epoch = 0
        self._preview_photo = None
        self._recording_in_progress = False
        self._abort_recording = False
        self._latest_frame = None

        self.control_widgets = []
        self._gui_queue = queue.Queue()
        self._camera_cmd_queue = queue.Queue()
        self._camera_thread = None
        self._shutdown_event = threading.Event()

        self.create_widgets()

        # Trace condition variables for live flow calculations
        self.kw_var.trace_add("write", lambda *_: self._update_flow_calculation())
        self.blend_gas_var.trace_add("write", lambda *_: self._update_flow_calculation())
        self.blend_var.trace_add("write", lambda *_: self._update_flow_calculation())
        self.phi_var.trace_add("write", lambda *_: self._update_flow_calculation())
        self._update_flow_calculation()

        self.init_camera()
        self._poll_gui_queue()

        if load_warnings:
            messagebox.showwarning("Settings", "\n".join(load_warnings))

    # ---------------------------------------------------------------- #
    # Widget construction
    # ---------------------------------------------------------------- #
    def create_widgets(self):
        self.root.configure(bg=COLORS["bg"])

        # 1. Header Bar
        header = tk.Frame(self.root, bg=COLORS["header_bg"])
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=COLORS["header_bg"])
        header_inner.pack(fill="x", padx=16, pady=10)

        tk.Label(
            header_inner, text="ReactingFlow Lab", bg=COLORS["header_bg"], fg="white", font=self.font_header,
        ).pack(side="left")
        tk.Label(
            header_inner, text="  Camera & Flow Controller", bg=COLORS["header_bg"], fg="#9fb3d9",
            font=self.font_subheader,
        ).pack(side="left", padx=(4, 0))

        try:
            self.img = tk.PhotoImage(file="ReFlowLab_signature.gif")
            tk.Label(header_inner, image=self.img, bg=COLORS["header_bg"]).pack(side="right", padx=(10, 0))
        except tk.TclError:
            self.img = None

        # Camera status badge in header
        self.header_status_badge = tk.Label(
            header_inner, text="● Camera: Initializing...", bg="#2b3b5c", fg="#9fb3d9",
            font=(self.font_family, 10, "bold"), padx=10, pady=4, relief="flat",
        )
        self.header_status_badge.pack(side="right", padx=(0, 10))

        # 2. Instant Tab Switcher (< 20ms switch time via grid stack)
        tab_nav_frame = tk.Frame(self.root, bg="#273552")
        tab_nav_frame.pack(fill="x")

        self.tab_buttons = {}
        for tab_id, tab_title in (("camera", "  📷  Camera & Flow Test  "), ("settings", "  ⚙️  Settings  ")):
            btn = tk.Button(
                tab_nav_frame, text=tab_title, font=self.font_button, relief="flat", bd=0, cursor="hand2",
                command=lambda tid=tab_id: self.switch_tab(tid), padx=16, pady=7,
            )
            btn.pack(side="left", padx=(2, 0))
            self.tab_buttons[tab_id] = btn

        # Container using Grid manager for instantaneous tab switching
        self.content_container = tk.Frame(self.root, bg=COLORS["bg"])
        self.content_container.pack(fill="both", expand=True)
        self.content_container.grid_rowconfigure(0, weight=1)
        self.content_container.grid_columnconfigure(0, weight=1)

        self.tab_camera = tk.Frame(self.content_container, bg=COLORS["bg"])
        self.tab_settings = tk.Frame(self.content_container, bg=COLORS["bg"])
        self.tab_camera.grid(row=0, column=0, sticky="nsew")
        self.tab_settings.grid(row=0, column=0, sticky="nsew")

        # Build contents of both tabs
        self._build_camera_tab(self.tab_camera)
        self._build_settings_tab(self.tab_settings)

        # Pre-warm both tabs to eliminate initial rendering freeze on first click
        self.tab_settings.tkraise()
        self.root.update()
        self.tab_camera.tkraise()
        self.root.update()

        self.current_tab = "camera"
        self._update_tab_button_styles("camera")

    def _update_tab_button_styles(self, active_tab_id):
        for tid, btn in self.tab_buttons.items():
            if tid == active_tab_id:
                btn.configure(bg=COLORS["surface"], fg=COLORS["primary"], activebackground=COLORS["surface"])
            else:
                btn.configure(bg="#273552", fg="#c3c9d4", activebackground="#334466")

    def switch_tab(self, tab_id):
        if tab_id == self.current_tab:
            return
        self.current_tab = tab_id
        self._update_tab_button_styles(tab_id)

        if tab_id == "settings":
            # Pause preview UI updates while viewing Settings to free up the event loop
            if self._preview_timer_id is not None:
                self.root.after_cancel(self._preview_timer_id)
                self._preview_timer_id = None
            self.tab_settings.tkraise()
        else:
            self.tab_camera.tkraise()
            # Resume preview UI updates if preview is active
            if self.preview_active and self._preview_timer_id is None:
                self._preview_tick()

    def _build_camera_tab(self, parent):
        # Prominent Error Banner (shown only on error)
        self.banner_frame = tk.Frame(parent, bg=COLORS["danger_light"], highlightbackground=COLORS["danger"], highlightthickness=1)
        self.banner_label = tk.Label(
            self.banner_frame, text="⚠️ CAMERA ERROR: No camera detected or connection lost.",
            bg=COLORS["danger_light"], fg=COLORS["danger_dark"], font=self.font_section, pady=6, padx=12,
        )
        self.banner_label.pack(side="left", padx=10)
        btn_retry = tk.Button(self.banner_frame, text="Retry Connection", command=self.init_camera)
        style_button(btn_retry, "danger", self.font_button)
        btn_retry.pack(side="right", padx=10, pady=3)

        frame_main = tk.Frame(parent, bg=COLORS["bg"])
        frame_main.pack(fill="both", expand=True)

        frame_left = tk.Frame(frame_main, bg=COLORS["bg"])
        frame_left.pack(side="left", fill="y", padx=12, pady=8)

        frame_right = tk.Frame(frame_main, bg=COLORS["bg"])
        frame_right.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=8)

        self._build_condition_frame(frame_left)
        self._build_flow_calc_frame(frame_left)
        self._build_controls_frame(frame_left)
        self._build_preview_frame(frame_right)

        self.frame_actions = tk.Frame(parent, bg=COLORS["bg"])
        self.frame_actions.pack(fill="x", padx=12, pady=(0, 8))
        self._build_action_frame(self.frame_actions)

    def _build_condition_frame(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Test condition ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=10, pady=5,
        )
        frame.pack(fill="x", pady=(0, 6))

        def add_picklist_row(row, label, key, var):
            tk.Label(
                frame, text=label, width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
            ).grid(row=row, column=0, sticky="w", pady=2)
            entry = tk.Entry(frame, textvariable=var, width=11, justify="center", state="readonly")
            style_entry(entry, self.font_base)
            entry.grid(row=row, column=1, padx=4)
            button = tk.Button(
                frame, text="Select", width=7,
                command=lambda key=key, label=label, var=var: self.show_picklist(
                    f"Select {label}", self.presets[key], var
                ),
            )
            style_button(button, "secondary", self.font_button)
            button.grid(row=row, column=2, padx=4)
            self.control_widgets.append(button)

        add_picklist_row(0, "Blending gas", "blend_gas", self.blend_gas_var)
        add_picklist_row(1, "Blending ratio (%)", "blend_ratio", self.blend_var)
        add_picklist_row(2, "Phi", "phi", self.phi_var)
        add_picklist_row(3, "Lance depth (mm)", "lance_depth", self.lance_depth_var)
        add_picklist_row(4, "Lance size (mm)", "lance_size", self.lance_size_var)

        tk.Label(
            frame, text="kW", width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
        ).grid(row=5, column=0, sticky="w", pady=2)
        kw_entry = tk.Entry(frame, textvariable=self.kw_var, width=11, justify="center")
        style_entry(kw_entry, self.font_base)
        kw_entry.grid(row=5, column=1, padx=4)
        self.control_widgets.append(kw_entry)

    def _build_flow_calc_frame(self, parent):
        """Card calculating NG1 flow, blend gas flow, air flow and expected flue gas O2 & CO2."""
        frame = tk.LabelFrame(
            parent, text=" Flow & Flue Gas Calculation (Nm³/h) ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=10, pady=5,
        )
        frame.pack(fill="x", pady=(0, 6))

        # Row 0: NG1 Flow
        tk.Label(
            frame, text="NG1 flow", width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"],
            font=self.font_metric_lbl,
        ).grid(row=0, column=0, sticky="w", pady=2)
        self.lbl_ng_flow = tk.Label(
            frame, textvariable=self.calc_ng_var, anchor="w", bg="#f0fdf4", fg="#166534",
            font=self.font_metric_val, padx=6, pady=2, relief="solid", bd=1,
            highlightbackground="#bbf7d0", highlightthickness=1, width=24,
        )
        self.lbl_ng_flow.grid(row=0, column=1, sticky="w", padx=4, pady=2)

        # Row 1: Blend Gas Flow
        self.lbl_blend_title = tk.Label(
            frame, textvariable=self.calc_blend_lbl_var, width=15, anchor="w", bg=COLORS["surface"],
            fg=COLORS["text"], font=self.font_metric_lbl,
        )
        self.lbl_blend_title.grid(row=1, column=0, sticky="w", pady=2)
        self.lbl_blend_flow = tk.Label(
            frame, textvariable=self.calc_blend_val_var, anchor="w", bg="#eff6ff", fg="#1e40af",
            font=self.font_metric_val, padx=6, pady=2, relief="solid", bd=1,
            highlightbackground="#bfdbfe", highlightthickness=1, width=24,
        )
        self.lbl_blend_flow.grid(row=1, column=1, sticky="w", padx=4, pady=2)

        # Row 2: Air Flow
        self.lbl_air_title = tk.Label(
            frame, textvariable=self.calc_air_lbl_var, width=15, anchor="w", bg=COLORS["surface"],
            fg=COLORS["text"], font=self.font_metric_lbl,
        )
        self.lbl_air_title.grid(row=2, column=0, sticky="w", pady=2)
        self.lbl_air_flow = tk.Label(
            frame, textvariable=self.calc_air_val_var, anchor="w", bg="#f8fafc", fg="#334155",
            font=self.font_metric_val, padx=6, pady=2, relief="solid", bd=1,
            highlightbackground="#cbd5e1", highlightthickness=1, width=24,
        )
        self.lbl_air_flow.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        # Row 3: Flue Gas O2
        tk.Label(
            frame, text="Flue gas O₂", width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"],
            font=self.font_metric_lbl,
        ).grid(row=3, column=0, sticky="w", pady=2)
        self.lbl_flue_o2 = tk.Label(
            frame, textvariable=self.calc_o2_var, anchor="w", bg="#fffbeb", fg="#b45309",
            font=self.font_metric_val, padx=6, pady=2, relief="solid", bd=1,
            highlightbackground="#fde68a", highlightthickness=1, width=24,
        )
        self.lbl_flue_o2.grid(row=3, column=1, sticky="w", padx=4, pady=2)

        # Row 4: Flue Gas CO2
        tk.Label(
            frame, text="Flue gas CO₂", width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"],
            font=self.font_metric_lbl,
        ).grid(row=4, column=0, sticky="w", pady=2)
        self.lbl_flue_co2 = tk.Label(
            frame, textvariable=self.calc_co2_var, anchor="w", bg="#faf5ff", fg="#6b21a8",
            font=self.font_metric_val, padx=6, pady=2, relief="solid", bd=1,
            highlightbackground="#e9d5ff", highlightthickness=1, width=24,
        )
        self.lbl_flue_co2.grid(row=4, column=1, sticky="w", padx=4, pady=2)

    def _update_flow_calculation(self):
        kw_val = self.kw_var.get()
        gas_val = self.blend_gas_var.get()
        ratio_val = self.blend_var.get()
        phi_val = self.phi_var.get()

        res = calculate_combustion_flow(kw_val, gas_val, ratio_val, phi_val)

        gas_upper = str(gas_val).strip().upper()
        if gas_upper in ("NONE", "", "PURE NG"):
            self.calc_blend_lbl_var.set("Blend gas flow")
            self.calc_blend_val_var.set("None (Pure NG)")
        elif gas_upper == "H2":
            self.calc_blend_lbl_var.set(f"H₂ flow ({ratio_val}%)")
        elif gas_upper == "NH3":
            self.calc_blend_lbl_var.set(f"NH₃ flow ({ratio_val}%)")
        else:
            self.calc_blend_lbl_var.set(f"{gas_val} flow ({ratio_val}%)")

        self.calc_air_lbl_var.set(f"Air flow (φ={phi_val})")

        if res["error"]:
            err = res["error"]
            self.calc_ng_var.set(f"— ({err})")
            if gas_upper not in ("NONE", "", "PURE NG"):
                self.calc_blend_val_var.set("—")
            self.calc_air_val_var.set("—")
            self.calc_o2_var.set("—")
            self.calc_co2_var.set("—")
            return

        self.calc_ng_var.set(f"{res['v_dot_ng']:.2f} Nm³/h")
        if gas_upper not in ("NONE", "", "PURE NG"):
            self.calc_blend_val_var.set(f"{res['v_dot_blend']:.2f} Nm³/h")
        self.calc_air_val_var.set(f"{res['v_dot_air']:.2f} Nm³/h")

        self.calc_o2_var.set(f"{res['o2_pct_dry']:.2f}% (Dry) | {res['o2_pct_wet']:.2f}% (Wet)")
        self.calc_co2_var.set(f"{res['co2_pct_dry']:.2f}% (Dry) | {res['co2_pct_wet']:.2f}% (Wet)")

    def _build_controls_frame(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Camera controls ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=10, pady=5,
        )
        frame.pack(fill="x", pady=(0, 6))

        def add_slider_row(row, label, var, lo, hi, resolution, attr_name):
            tk.Label(
                frame, text=label, width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
            ).grid(row=row, column=0, sticky="w", pady=2)
            scale = tk.Scale(
                frame, from_=lo, to=hi, resolution=resolution, orient="horizontal",
                variable=var, length=180, showvalue=0, command=self._on_slider_move,
                bg=COLORS["surface"], troughcolor="#c3c9d4", fg=COLORS["primary"],
                activebackground=COLORS["primary"], highlightthickness=0, bd=0, sliderrelief="flat",
            )
            scale.grid(row=row, column=1, padx=4)
            scale.bind("<ButtonRelease-1>", lambda e: self._apply_controls())
            entry = tk.Entry(frame, textvariable=var, width=7, justify="center")
            style_entry(entry, self.font_base)
            entry.grid(row=row, column=2, padx=4)
            entry.bind("<Return>", self._apply_controls)
            entry.bind("<FocusOut>", self._apply_controls)
            self.control_widgets.extend([scale, entry])
            setattr(self, attr_name, scale)
            return scale, entry

        shutter_lo, shutter_hi = self.ranges["shutter"]
        gain_lo, gain_hi = self.ranges["gain"]
        focus_lo, focus_hi = self.ranges["focus_cm"]

        add_slider_row(0, "Shutter (us)", self.shutter_var, shutter_lo, shutter_hi, 100, "shutter_scale")
        add_slider_row(1, "Gain", self.gain_var, gain_lo, gain_hi, 0.1, "gain_scale")

        # Focus Distance in Centimeters (1/f corrected)
        self.focus_scale, self.focus_entry = add_slider_row(
            2, "Focus dist (cm)", self.focus_cm_var, focus_lo, focus_hi, 1.0, "focus_scale"
        )

        # Dioptre readback label (shows the equivalent 1/f dioptres in real time)
        self.focus_dioptre_label = tk.Label(
            frame, text="", bg=COLORS["surface"], fg=COLORS["text_muted"], font=self.font_base,
        )
        self.focus_dioptre_label.grid(row=2, column=3, sticky="w")
        self._update_focus_dioptre_display()

        tk.Label(
            frame, text="AWB red gain", width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
        ).grid(row=3, column=0, sticky="w", pady=2)
        awb_r_entry = tk.Entry(frame, textvariable=self.awb_r_var, width=7, justify="center")
        style_entry(awb_r_entry, self.font_base)
        awb_r_entry.grid(row=3, column=1, padx=4, sticky="w")
        awb_r_entry.bind("<Return>", self._apply_controls)
        awb_r_entry.bind("<FocusOut>", self._apply_controls)

        tk.Label(
            frame, text="AWB blue gain", width=15, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
        ).grid(row=4, column=0, sticky="w", pady=2)
        awb_b_entry = tk.Entry(frame, textvariable=self.awb_b_var, width=7, justify="center")
        style_entry(awb_b_entry, self.font_base)
        awb_b_entry.grid(row=4, column=1, padx=4, sticky="w")
        awb_b_entry.bind("<Return>", self._apply_controls)
        awb_b_entry.bind("<FocusOut>", self._apply_controls)

        chk = tk.Checkbutton(
            frame, text="Swap Red/Blue (RGB ↔ BGR)", variable=self.swap_rb_var,
            command=self._on_swap_rb_changed, bg=COLORS["surface"], fg=COLORS["text"],
            activebackground=COLORS["surface"], font=self.font_base,
        )
        chk.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 2))

        self.control_widgets.extend([awb_r_entry, awb_b_entry, chk])

    def _update_focus_dioptre_display(self):
        try:
            dist = float(self.focus_cm_var.get())
            d = dist_cm_to_dioptres(dist)
            self.focus_dioptre_label.config(text=f"(≈ {d:.2f} D)")
        except Exception:
            self.focus_dioptre_label.config(text="")

    def _on_slider_move(self, *_):
        self._update_focus_dioptre_display()
        if self._slider_debounce_id is not None:
            self.root.after_cancel(self._slider_debounce_id)
        self._slider_debounce_id = self.root.after(35, self._apply_controls)

    def _on_swap_rb_changed(self):
        self.swap_rb = self.swap_rb_var.get()
        self.defaults["swap_rb"] = self.swap_rb
        try:
            save_settings(self.presets, self.defaults, self.ranges)
        except Exception:
            pass

    def _build_preview_frame(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Live preview ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=12, pady=6,
        )
        frame.pack(fill="both", expand=True)

        self._black_frame_img = Image.new("RGB", PREVIEW_SIZE, "black")
        self._preview_photo = ImageTk.PhotoImage(self._black_frame_img)
        preview_border = tk.Frame(frame, bg=COLORS["header_bg"], padx=2, pady=2)
        preview_border.pack(pady=(0, 6))
        self.preview_label = tk.Label(preview_border, image=self._preview_photo, bg="black")
        self.preview_label.pack()

        tk.Label(
            frame, text="Snapshots", bg=COLORS["surface"], fg=COLORS["text_muted"], font=self.font_base,
        ).pack(anchor="w")
        self.snapshot_strip = tk.Frame(frame, bg=COLORS["surface"])
        self.snapshot_strip.pack(pady=(2, 4), fill="x")
        self._snapshot_thumb_labels = []
        self._snapshot_thumb_photos = []

        self.button_preview = tk.Button(frame, text="Preview", width=14, command=self.toggle_preview)
        style_button(self.button_preview, "primary", self.font_button)
        self.button_preview.pack(pady=4)

    def _clear_snapshot_thumbnails(self):
        for label in self._snapshot_thumb_labels:
            label.destroy()
        self._snapshot_thumb_labels = []
        self._snapshot_thumb_photos = []

    def _add_snapshot_thumbnail(self, pil_thumb, snap_index):
        photo = ImageTk.PhotoImage(pil_thumb)
        self._snapshot_thumb_photos.append(photo)
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
        inner = tk.Frame(card, bg=COLORS["surface"], padx=12, pady=8)
        inner.pack(fill="x")

        tk.Label(inner, text="Duration (s)", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base).grid(
            row=0, column=0, padx=(0, 6)
        )
        duration_entry = tk.Entry(inner, textvariable=self.duration_var, width=8, justify="center")
        style_entry(duration_entry, self.font_base)
        duration_entry.grid(row=0, column=1, padx=(0, 14))

        tk.Label(inner, text="Snapshots", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base).grid(
            row=0, column=2, padx=(0, 6)
        )
        snaps_entry = tk.Entry(inner, textvariable=self.n_snaps_var, width=8, justify="center")
        style_entry(snaps_entry, self.font_base)
        snaps_entry.grid(row=0, column=3, padx=(0, 14))

        self.button_record = tk.Button(inner, text="Record", width=14, command=self.on_record_clicked)
        style_button(self.button_record, "primary", self.font_button)
        self.button_record.grid(row=0, column=4, padx=(0, 10))

        self.button_emergency_stop = tk.Button(
            inner, text="EMERGENCY STOP", width=16, state=tk.DISABLED, command=self._on_emergency_stop,
        )
        style_button(self.button_emergency_stop, "danger", self.font_button)
        self.button_emergency_stop.grid(row=0, column=5)

        self.control_widgets.extend([duration_entry, snaps_entry, self.button_record])

        tk.Label(
            inner, textvariable=self.status_var, bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        self._last_output_folder = None
        self.folder_link_var = tk.StringVar(value="")
        self.label_folder_link = tk.Label(
            inner, textvariable=self.folder_link_var, bg=COLORS["surface"], fg=COLORS["primary"],
            cursor="hand2", font=(self.font_family, 10, "underline"),
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

    # ---------------------------------------------------------------- #
    # Settings Tab (Lightweight layout for fast rendering)
    # ---------------------------------------------------------------- #
    def _build_settings_tab(self, parent):
        self._settings_form_vars = {}
        outer = tk.Frame(parent, bg=COLORS["bg"], padx=18, pady=14)
        outer.pack(fill="both", expand=True)

        def add_form_var(key, value):
            var = tk.StringVar(value=str(value))
            self._settings_form_vars[key] = var
            return var

        def col_header(container, text, col, anchor="w"):
            sticky = anchor if anchor in ("w", "e") else ""
            tk.Label(
                container, text=text, font=(self.font_family, 9, "bold"), bg=COLORS["surface"],
                fg=COLORS["text_muted"], anchor=anchor,
            ).grid(row=0, column=col, sticky=sticky, pady=(0, 5))

        def row_label(container, text, row):
            tk.Label(
                container, text=text, width=18, anchor="w", bg=COLORS["surface"], fg=COLORS["text"],
                font=self.font_base,
            ).grid(row=row, column=0, sticky="w", pady=2)

        def make_entry(container, var, row, col, width=10, sticky=None, padx=6):
            entry = tk.Entry(container, textvariable=var, width=width, justify="center")
            style_entry(entry, self.font_base)
            entry.grid(row=row, column=col, padx=padx, sticky=sticky)
            return entry

        # Presets frame
        preset_frame = tk.LabelFrame(
            outer, text=" Test condition presets & defaults ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=12, pady=6,
        )
        preset_frame.pack(fill="x", pady=(0, 8))
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
            make_entry(preset_frame, values_var, row, 1, width=42, sticky="w")
            default_var = add_form_var(f"{key}_default", self.defaults[key])
            make_entry(preset_frame, default_var, row, 2)

        # Ranges frame
        range_frame = tk.LabelFrame(
            outer, text=" Camera control ranges & defaults ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=12, pady=6,
        )
        range_frame.pack(fill="x", pady=(0, 8))
        col_header(range_frame, "Control", 0)
        col_header(range_frame, "Min", 1)
        col_header(range_frame, "Max", 2)
        col_header(range_frame, "Default", 3)

        range_fields = [
            ("shutter", "Shutter (us)"),
            ("gain", "Gain"),
            ("focus_cm", "Focus distance (cm)"),
        ]
        for row, (key, label) in enumerate(range_fields, start=1):
            row_label(range_frame, label, row)
            lo_var = add_form_var(f"{key}_min", self.ranges[key][0])
            hi_var = add_form_var(f"{key}_max", self.ranges[key][1])
            default_var = add_form_var(f"{key}_default", self.defaults[key])
            make_entry(range_frame, lo_var, row, 1)
            make_entry(range_frame, hi_var, row, 2)
            make_entry(range_frame, default_var, row, 3)

        # Single values frame
        other_frame = tk.LabelFrame(
            outer, text=" Other defaults ", font=self.font_section,
            bg=COLORS["surface"], fg=COLORS["text"], bd=1, relief="solid",
            highlightbackground=COLORS["border"], highlightthickness=1, padx=12, pady=6,
        )
        other_frame.pack(fill="x", pady=(0, 8))

        single_fields = [
            ("kw", "kW"),
            ("awb_r", "AWB red gain"),
            ("awb_b", "AWB blue gain"),
            ("duration", "Duration (s)"),
            ("n_snaps", "Snapshot count"),
        ]
        for idx, (key, label) in enumerate(single_fields):
            row, col = divmod(idx, 2)
            tk.Label(
                other_frame, text=label, anchor="w", bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base,
            ).grid(row=row, column=col * 2, sticky="w", padx=(10, 4), pady=2)
            var = add_form_var(f"{key}_default", self.defaults[key])
            make_entry(other_frame, var, row, col * 2 + 1, width=10)

        # Buttons row
        btn_bar = tk.Frame(outer, bg=COLORS["bg"])
        btn_bar.pack(fill="x", pady=8)

        btn_apply = tk.Button(btn_bar, text="Apply Changes", width=14, command=self._on_settings_apply)
        style_button(btn_apply, "primary", self.font_button)
        btn_apply.pack(side="left", padx=(0, 10))

        btn_restore = tk.Button(btn_bar, text="Restore Defaults", width=16, command=self._on_settings_restore_factory)
        style_button(btn_restore, "secondary", self.font_button)
        btn_restore.pack(side="left")

        self.settings_status_var = tk.StringVar(value="")
        tk.Label(
            btn_bar, textvariable=self.settings_status_var, bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=self.font_base,
        ).pack(side="left", padx=16)

    def _refresh_settings_form(self):
        for key in FACTORY_PRESETS:
            self._settings_form_vars[f"{key}_values"].set(", ".join(str(v) for v in self.presets[key]))
            self._settings_form_vars[f"{key}_default"].set(str(self.defaults[key]))
        for key in FACTORY_RANGES:
            lo, hi = self.ranges[key]
            self._settings_form_vars[f"{key}_min"].set(str(lo))
            self._settings_form_vars[f"{key}_max"].set(str(hi))
            self._settings_form_vars[f"{key}_default"].set(str(self.defaults[key]))
        for key in ("kw", "awb_r", "awb_b", "duration", "n_snaps"):
            self._settings_form_vars[f"{key}_default"].set(str(self.defaults[key]))

    def _reconfigure_ranges(self):
        lo, hi = self.ranges["shutter"]
        self.shutter_scale.config(from_=lo, to=hi)
        lo, hi = self.ranges["gain"]
        self.gain_scale.config(from_=lo, to=hi)
        lo, hi = self.ranges["focus_cm"]
        self.focus_scale.config(from_=lo, to=hi)

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
        self.focus_cm_var.set(self.defaults["focus_cm"])
        self.duration_var.set(_fmt_num(self.defaults["duration"]))
        self.n_snaps_var.set(str(self.defaults["n_snaps"]))
        self._update_focus_dioptre_display()
        self._update_flow_calculation()
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

        defaults["swap_rb"] = getattr(self, "swap_rb", False)
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
        self.swap_rb = bool(self.defaults.get("swap_rb", False))
        self.swap_rb_var.set(self.swap_rb)
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
    # Event Queue & Preview cadence
    # ---------------------------------------------------------------- #
    def _poll_gui_queue(self):
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
            self.root.after(80, self._poll_gui_queue)

    def _preview_tick(self):
        if not self.preview_active:
            return  # Stop running when preview is inactive

        try:
            # Only render image to UI if camera tab is currently front
            if self.current_tab == "camera":
                frame_data = self._latest_frame
                if frame_data is not None:
                    self._latest_frame = None
                    pil_img, epoch = frame_data
                    self._render_preview_frame(pil_img, epoch)
        finally:
            if self.preview_active:
                self._preview_timer_id = self.root.after(PREVIEW_INTERVAL_MS, self._preview_tick)

    # ---------------------------------------------------------------- #
    # Picklist Popup
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
            sub_window, text=title, bg=COLORS["surface"], fg=COLORS["text"], font=self.font_section, pady=10,
        ).pack(padx=20)
        for v in values:
            tk.Radiobutton(
                sub_window, text=str(v), value=str(v), variable=local_choice,
                bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base, selectcolor=COLORS["primary_light"],
                activebackground=COLORS["surface"], anchor="w", padx=20,
            ).pack(fill="x", padx=20)

        button = tk.Button(sub_window, text="Select", width=10, command=select)
        style_button(button, "primary", self.font_button)
        button.pack(pady=12)

    # ---------------------------------------------------------------- #
    # Camera Lifecycle & Error Handling
    # ---------------------------------------------------------------- #
    def _idle_camera_config(self):
        ctrls = {"FrameRate": 30}
        if self.picam2 is not None and "AfMode" in self.picam2.camera_controls:
            ctrls["AfMode"] = controls.AfModeEnum.Manual
        return self.picam2.create_preview_configuration(
            main={"size": PREVIEW_SIZE, "format": "BGR888"},
            transform=Transform(hflip=1, vflip=1),
            controls=ctrls,
        )

    def _recording_camera_config(self):
        ctrls = {"FrameRate": 30}
        if self.picam2 is not None and "AfMode" in self.picam2.camera_controls:
            ctrls["AfMode"] = controls.AfModeEnum.Manual
        return self.picam2.create_video_configuration(
            main={"size": MAIN_SIZE, "format": "BGR888"},
            lores={"size": PREVIEW_SIZE, "format": "BGR888"},
            display="lores",
            transform=Transform(hflip=1, vflip=1),
            controls=ctrls,
        )

    def init_camera(self):
        try:
            if self.picam2 is not None:
                try:
                    self.picam2.close()
                except Exception:
                    pass

            self.header_status_badge.config(text="● Camera: Connecting...", bg="#d97706", fg="#fef3c7")
            self.picam2 = Picamera2()
            self.picam2.configure(self._idle_camera_config())
            self._preview_stream_name = "main"

            self.camera_ready = True
            self.header_status_badge.config(text="● Camera: Connected (Ready)", bg="#15803d", fg="#dcfce7")
            self.banner_frame.pack_forget()

            self._set_controls_enabled(True)
            self.button_preview.config(state=tk.NORMAL)
            self.button_record.config(state=tk.NORMAL)
            self.status_var.set("Camera connected successfully.")

            if self._camera_thread is None or not self._camera_thread.is_alive():
                self._shutdown_event.clear()
                self._camera_thread = threading.Thread(target=self._camera_thread_fn, daemon=True)
                self._camera_thread.start()

            self._apply_controls()

        except Exception as exc:
            self._handle_camera_error(f"Initialization failed: {exc}")

    def _show_error_on_preview_screen(self, title, msg):
        """Render a high-visibility connection error graphic directly on the preview screen."""
        img = Image.new("RGB", PREVIEW_SIZE, color="#1e1e24")
        draw = ImageDraw.Draw(img)
        w, h = PREVIEW_SIZE

        # Draw red warning border
        draw.rectangle([2, 2, w - 3, h - 3], outline="#ef4444", width=3)
        # Warning triangle icon
        draw.polygon([(w // 2, 70), (w // 2 - 40, 140), (w // 2 + 40, 140)], fill="#ef4444")
        draw.rectangle([(w // 2 - 2, 95), (w // 2 + 2, 120)], fill="white")
        draw.ellipse([(w // 2 - 2, 126), (w // 2 + 2, 130)], fill="white")

        def draw_centered(y, text, fill="white"):
            bbox = draw.textbbox((0, 0), text)
            tw = bbox[2] - bbox[0]
            draw.text(((w - tw) // 2, y), text, fill=fill)

        draw_centered(150, title, "#fee2e2")
        draw_centered(170, str(msg)[:60], "#fca5a5")
        draw_centered(210, "Please check CSI ribbon cable / hardware connection.", "#9ca3af")
        self._preview_photo = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=self._preview_photo)

    def _handle_camera_error(self, err_msg):
        self.camera_ready = False
        self.preview_active = False
        self.header_status_badge.config(text="● Camera: DISCONNECTED / ERROR", bg="#991b1b", fg="#fecaca")

        # Show prominent red alert banner at the top of Camera tab
        self.banner_label.config(text=f"⚠️ CAMERA ERROR: {err_msg}")
        self.banner_frame.pack(fill="x", padx=12, pady=(8, 0), before=self.tab_camera.winfo_children()[1])

        self.status_var.set(f"CAMERA ERROR: {err_msg}")
        self.button_preview.config(state=tk.DISABLED, text="Preview")
        self.button_record.config(state=tk.DISABLED)
        self._set_controls_enabled(False)
        self._show_error_on_preview_screen("CAMERA CONNECTION ERROR", str(err_msg)[:60])

    def on_close(self):
        if self._recording_in_progress:
            if not messagebox.askokcancel("Recording in progress", "A recording is still running. Quit anyway?"):
                return
            self._abort_recording = True

        self.preview_active = False
        self._shutdown_event.set()
        if self._camera_thread is not None:
            self._camera_thread.join(timeout=3)

        if self.picam2 is not None:
            try:
                self.picam2.close()
            except Exception:
                pass

        self.root.destroy()

    # ---------------------------------------------------------------- #
    # Live preview
    # ---------------------------------------------------------------- #
    def toggle_preview(self):
        if not self.camera_ready:
            return
        self.preview_active = not self.preview_active
        self._preview_epoch += 1

        if self.preview_active:
            self.button_preview.config(text="Stop Preview")
            self._camera_cmd_queue.put(("start_preview",))
            self._preview_tick()
        else:
            self.button_preview.config(text="Preview")
            self._latest_frame = None
            self._camera_cmd_queue.put(("stop_preview",))
            self._preview_photo = ImageTk.PhotoImage(self._black_frame_img)
            self.preview_label.configure(image=self._preview_photo)

    def _render_preview_frame(self, pil_img, epoch):
        if epoch != self._preview_epoch:
            return
        photo = ImageTk.PhotoImage(pil_img)
        self.preview_label.configure(image=photo)
        self._preview_photo = photo

    # ---------------------------------------------------------------- #
    # Live controls
    # ---------------------------------------------------------------- #
    def _current_controls_dict(self):
        ctrls = {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": int(self.shutter_var.get()),
            "AnalogueGain": float(self.gain_var.get()),
            "ColourGains": (float(self.awb_r_var.get()), float(self.awb_b_var.get())),
        }
        if self.picam2 is not None and "AfMode" in self.picam2.camera_controls:
            ctrls["AfMode"] = controls.AfModeEnum.Manual

        # Convert Distance (cm) -> LensPosition (dioptres = 1/m)
        dist_cm = float(self.focus_scale.get() if hasattr(self, "focus_scale") else self.focus_cm_var.get())
        lens_dioptres = dist_cm_to_dioptres(dist_cm)

        if self.picam2 is not None:
            if "LensPosition" in self.picam2.camera_controls:
                ctrls["LensPosition"] = lens_dioptres
        else:
            ctrls["LensPosition"] = lens_dioptres
        return ctrls

    def _apply_controls(self, *_):
        self._update_focus_dioptre_display()
        if not self.camera_ready:
            return
        try:
            ctrls = self._current_controls_dict()
        except (tk.TclError, ValueError) as exc:
            self.status_var.set(f"Control update failed: {exc}")
            return
        self._camera_cmd_queue.put(("apply_controls", ctrls))

    # ---------------------------------------------------------------- #
    # Camera thread (Zero-idle CPU load + robust error detection)
    # ---------------------------------------------------------------- #
    def _camera_thread_fn(self):
        is_streaming = False
        last_health_check_t = 0.0

        while not self._shutdown_event.is_set():
            # If preview is off and not recording, block up to 0.4s to consume zero CPU
            timeout = 0.02 if (self.preview_active or self._recording_in_progress) else 0.4
            try:
                cmd = self._camera_cmd_queue.get(timeout=timeout)
            except queue.Empty:
                cmd = None

            if cmd is not None:
                kind = cmd[0]
                if kind == "start_preview":
                    if not is_streaming and self.picam2 is not None:
                        try:
                            self.picam2.start()
                            is_streaming = True
                            self.picam2.set_controls(self._current_controls_dict())
                        except Exception as exc:
                            self._gui_queue.put((self._handle_camera_error, (f"Preview start failed: {exc}",)))
                elif kind == "stop_preview":
                    if is_streaming and not self._recording_in_progress and self.picam2 is not None:
                        try:
                            self.picam2.stop()
                            is_streaming = False
                        except Exception:
                            pass
                elif kind == "apply_controls":
                    latest_controls = cmd[1]
                    while not self._camera_cmd_queue.empty():
                        try:
                            peek = self._camera_cmd_queue.get_nowait()
                            if peek[0] == "apply_controls":
                                latest_controls = peek[1]
                            else:
                                self._camera_cmd_queue.put(peek)
                                break
                        except queue.Empty:
                            break
                    if self.picam2 is not None:
                        try:
                            self.picam2.set_controls(latest_controls)
                        except Exception as exc:
                            self._gui_queue.put((self._set_status, (f"Control update failed: {exc}",)))
                elif kind == "record":
                    _, output_folder, duration_s, n_snaps = cmd
                    is_streaming = False
                    self._do_record(output_folder, duration_s, n_snaps)
                    continue

            # Capture preview frames when preview is active
            if self.preview_active and not self._recording_in_progress and is_streaming:
                epoch = self._preview_epoch
                try:
                    frame = self.picam2.capture_array(self._preview_stream_name)
                    if self.swap_rb:
                        frame = frame[:, :, ::-1]
                    pil_img = Image.fromarray(frame)
                    self._latest_frame = (pil_img, epoch)
                except Exception as exc:
                    self._gui_queue.put((self._handle_camera_error, (f"Capture error: {exc}",)))
                    time.sleep(0.1)
            elif not is_streaming and not self._recording_in_progress:
                # Periodic connection health check while idle (every 3 seconds)
                now = time.monotonic()
                if now - last_health_check_t >= 3.0:
                    last_health_check_t = now
                    if self.picam2 is not None:
                        try:
                            if not os.path.exists("/dev/media3"):
                                raise RuntimeError("Camera device node disconnected")
                        except Exception as exc:
                            self._gui_queue.put((self._handle_camera_error, (f"Hardware disconnected: {exc}",)))

        # Clean shutdown
        if is_streaming and self.picam2 is not None:
            try:
                self.picam2.stop()
            except Exception:
                pass

    # ---------------------------------------------------------------- #
    # Folder naming
    # ---------------------------------------------------------------- #
    def build_output_folder(self):
        date_str = datetime.now().strftime("%Y%m%d")
        gas = self.blend_gas_var.get().strip()
        ratio = self.blend_var.get().strip()
        if gas.lower() in ("none", "pure ng", "pureng"):
            gas_str = "PureNG"
        else:
            gas_str = f"{gas}blend{ratio}pc"

        base_name = (
            f"{gas_str}_"
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
            dialog, bg=COLORS["surface"], fg=COLORS["text"], font=self.font_base, pady=14, justify="left",
            text=(
                f"A recording already exists for this exact condition today:\n\n"
                f"{os.path.basename(candidate)}\n\n"
                "Overwrite it, or save this run separately?"
            ),
        ).pack(padx=20)

        button_row = tk.Frame(dialog, bg=COLORS["surface"])
        button_row.pack(pady=14)
        btn_overwrite = tk.Button(button_row, text="Overwrite", width=12, command=lambda: pick("overwrite"))
        style_button(btn_overwrite, "danger", self.font_button)
        btn_overwrite.pack(side="left", padx=5)
        btn_new_run = tk.Button(button_row, text="New Run", width=12, command=lambda: pick("new_run"))
        style_button(btn_new_run, "primary", self.font_button)
        btn_new_run.pack(side="left", padx=5)
        btn_cancel = tk.Button(button_row, text="Cancel", width=12, command=lambda: pick("cancel"))
        style_button(btn_cancel, "secondary", self.font_button)
        btn_cancel.pack(side="left", padx=5)

        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["choice"]

    # ---------------------------------------------------------------- #
    # Validation & Widget Control
    # ---------------------------------------------------------------- #
    def _validate_all(self):
        if self.kw_var.get().strip() == "":
            messagebox.showinfo("Data not found", "Please enter the kW value.")
            return False
        try:
            kw = float(self.kw_var.get())
            if kw <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value", "kW must be a positive number.")
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
    # Recording workflow
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

        if not self.preview_active:
            self.toggle_preview()

        self._camera_cmd_queue.put(("record", output_folder, duration_s, n_snaps))

    def _on_emergency_stop(self):
        if not self._recording_in_progress:
            return
        self._abort_recording = True
        self.status_var.set("Stopping recording...")
        self.button_emergency_stop.config(state=tk.DISABLED)

    def _do_record(self, output_folder, duration_s, n_snaps):
        error = None
        recording_started = False
        try:
            try:
                self.picam2.stop()
            except Exception:
                pass

            self.picam2.configure(self._recording_camera_config())
            self._preview_stream_name = "lores"
            self.picam2.start()
            self.picam2.set_controls(self._current_controls_dict())

            encoder = H264Encoder()
            video_path = os.path.join(output_folder, "video.mp4")
            self.picam2.start_recording(encoder, FfmpegOutput(video_path))
            recording_started = True

            start_time = time.monotonic()
            end_time = start_time + duration_s
            snap_interval = duration_s / (n_snaps + 1) if n_snaps > 0 else None
            snap_times = [start_time + i * snap_interval for i in range(1, n_snaps + 1)] if snap_interval else []
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
                        frame = self.picam2.capture_array(self._preview_stream_name)
                        if self.swap_rb:
                            frame = frame[:, :, ::-1]
                        pil_img = Image.fromarray(frame)
                        self._latest_frame = (pil_img, epoch)
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
            if recording_started:
                try:
                    self.picam2.stop_recording()
                except Exception:
                    pass

            try:
                self.picam2.stop()
            except Exception:
                pass

            try:
                self.picam2.configure(self._idle_camera_config())
                self._preview_stream_name = "main"
                if self.preview_active:
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
