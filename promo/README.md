# Arctis promo

A local, 30-second feature promo, with landscape as the default:

- Landscape: 1920×1080, 30 fps, H.264 / AAC.
- Optional vertical export: 1080×1920, 30 fps, H.264 / AAC.

The cuts use the plugin's actual `ArctisPanel.qml`, rendered with isolated
sample data and a capture-only `KeyboardPanel` wrapper. They do not open the
live panel, read microphone/desktop audio, or change headset/output settings.
The frame is labelled **DEMO**. Theme overrides apply only inside the preview
process. All music is synthesized locally by `render.py`; no stock music or
external samples are used.

The real plugin takes the right half of the landscape frame at 880 pixels
wide. The ChatMix scene shows the plugin's own balance readout; there are
no duplicate Game/Chat bars outside it. The expanded-settings scene uses
a labelled close-up of the actual settings controls at the same scale.
During seconds 3–9, the soundtrack's melodic foreground sweeps smoothly
from left to right using equal-power panning; bass and drums stay centered.
This stereo effect is part of the promo soundtrack.

## Storyboard

| Time | Feature | Caption |
| --- | --- | --- |
| 0–3 s | Introduction | Game. Chat. In balance. |
| 3–9 s | Hardware ChatMix | One wheel. Two channels. |
| 9–15 s | Live volume and saved settings | Make it sound right. |
| 15–22 s | Alternate output and return | Headset off? Keep listening. |
| 22–26 s | Battery and app routing | Small panel. Full control. |
| 26–30 s | Source repository | Arctis for Omarchy. |

## Rebuild

Requires Omarchy Quattro / Quickshell, Python with Pillow and NumPy, FFmpeg
with libx264 and AAC, and the system Noto Sans / Caskaydia Mono fonts. Font
files are not bundled. No web services or downloaded assets are needed.

From the repository root:

```sh
promo_dir="$HOME/Videos/Arctis Promo"
python3 promo/capture.py "$promo_dir/build/ui"
python3 promo/render.py "$promo_dir/build/ui" "$promo_dir/review" --stills
python3 promo/render.py "$promo_dir/build/ui" "$promo_dir" --suffix=-v3
```

Use `--format both` or `--format vertical` for additional formats. The
default is landscape. `--suffix` gives revised exports distinct filenames
to avoid stale player caches. The capture step creates 900 PNG frames plus height
metadata; the render step also saves the original stereo music bed.
Build files and videos stay outside the repository.

`preview.qml.in` is a QML template populated by `capture.py`. The offscreen
preview uses links to the installed shell's shared controls and an isolated
wrapper instead of its real layer-shell window. The plugin source itself
is left unchanged. Each capture waits for native layout to settle before
measuring its dimensions. Panel changes use a short fade through transparent
at 12, 15, 22 and 26 seconds, with no animated stretching or cropping.

## Validation

Check each MP4 with `ffprobe` for exactly 30 seconds / 900 video frames,
the intended dimensions, yuv420p, BT.709 and stereo AAC. Decode the entire
file with `ffmpeg -v error -i <file> -f null -`. Review representative
frames in every scene and the expanded-settings transition. The soundtrack
is normalized to approximately −14 LUFS with a true-peak ceiling of
−1.5 dBFS. The video includes no narration; captions are part of the image.

Creating these files does not publish a release, upload a video, or submit
anything to the plugin marketplace.
