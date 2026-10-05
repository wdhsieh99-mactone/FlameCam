# ReactingFlow Lab - FlameCam Camera & Flow Controller

FlameCam is a Python Tkinter GUI application designed for the ReactingFlow Lab. It interfaces with a Raspberry Pi Camera Module 3 (Sony IMX708) to perform manual and automated camera diagnostics, focus adjustment, high-resolution recording, snapshots, and real-time combustion gas/air flow calculations.

## Features (GUI_17.py)

- **Camera Control & Live Preview**:
  - Continuous 30 fps live preview with zero-idle CPU load.
  - Manual focus adjustment with $1/f$ distance mapping in centimeters (`4.0` cm to `300.0` cm) and real-time dioptre readback ($D = 100 / \text{dist}$).
  - Live shutter speed ($\mu\text{s}$), analogue gain, manual AWB red/blue gains, and RGB/BGR swap toggle.
  - 3-level hardware connection monitoring (header status badge, top warning banner, and live preview graphic overlay).

- **Region of Interest (ROI) & Crop**:
  - Interactive click-and-drag rectangle selection on the live preview screen.
  - Dynamic visual feedback: area outside the selected ROI is dimmed to 25% brightness while the ROI remains at 100% full brightness with an emerald border.
  - **Snapshots**: Automatically crops high-resolution capture frames (`snapXX.jpg`) to the active ROI.
  - **Video Recording**: Uses FFmpeg to crop recorded video (`video.mp4`) to the exact ROI bounding box.

- **Combustion & Flow Calculation Panel**:
  - Natural Gas calculation based on **Alicat MFC Nat Gas 1 (NG1)** composition ($93\%\text{ CH}_4, 3\%\text{ C}_2\text{H}_6, 1\%\text{ C}_3\text{H}_8, 2\%\text{ N}_2, 1\%\text{ CO}_2$).
  - Blending gases: **`None` (Pure NG)**, **`H2`**, and **`NH3`**.
  - Maintains total heat input (thermal power in kW) across volume blending ratios ($r_v$) using lower heating values (LHV).
  - Calculates actual air flow requirements via equivalence ratio ($\phi$).
  - Displays expected flue gas $\text{O}_2$ and $\text{CO}_2$ percentages on both **Dry basis** and **Wet basis**.

- **High Performance UI**:
  - Instantaneous tab switching (< 1 ms response on mouse down).
  - Aligned, balanced dual-column layout with synchronized preview frame height.

## Requirements

- Raspberry Pi 4 / 5 running Raspberry Pi OS (Bookworm)
- Python 3.10+
- `picamera2`, `libcamera`
- `pillow`, `numpy`, `ffmpeg`

## Usage

```bash
python3 GUI_17.py
```
