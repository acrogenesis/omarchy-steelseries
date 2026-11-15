# Arctis for Omarchy

Arctis Nova 7X (`1038:22a5`) integration for the Omarchy Quattro shell.

- Headset power / link status, battery percentage and charging indicator.
- Two stereo outputs: **Arctis Game** and **Arctis Chat**.
- Hardware ChatMix wheel balances the two outputs, with both full at center.
- Separate Game and Chat volume trims; normal volume keys remain usable.
- Remember application assignments; Discord, Vesktop, Zoom and communication-role streams default to Chat.
- Low battery alert at 20%, optional.
- Choose another output for Game/Chat while the headset is off; automatically return to the headset when it turns on. The choice is saved, and no output is selected automatically.
- Send sidetone and idle shutoff settings to the headset. Each successful choice is saved independently and marked with a check, including after reopening the panel or restarting the shell. The saved choices are not device readback; changes made in another app cannot be detected on this model. Opening the panel does not resend settings.

The wheel only affects apps routed through these virtual outputs. Bluetooth audio arriving directly at the headset is outside PipeWire. The headset must use its USB dongle in PC/USB mode. App rules apply only to streams already using the headset or its virtual outputs. Browser tabs share the browser's assignment. Use the headset's physical microphone mute button and red LED. The current HeadsetControl backend does not expose physical mute status for this model, so the plugin does not show a mute indicator.

## Dependencies

Python 3, `pipewire`, `pipewire-pulse` (`pactl`, `pw-loopback`), `hidapi`, and [HeadsetControl](https://github.com/Sapd/HeadsetControl) with `22a5` support. `notify-send` is optional. No network requests are made at runtime.

For the initial development install, HeadsetControl was built from upstream commit `0c8a76fa019cd02263a1c69e00e18e229b16d87f` and installed at `~/.local/lib/omarchy-arctis/headsetcontrol`. An installed `headsetcontrol` on PATH takes precedence. HeadsetControl is a separate GPL dependency; its source and build instructions are upstream. Building it:

```sh
git clone https://github.com/Sapd/HeadsetControl.git
cd HeadsetControl
git checkout 0c8a76fa019cd02263a1c69e00e18e229b16d87f
cmake -S . -B build -DBUILD_UNIT_TESTS=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 8
install -Dm755 build/headsetcontrol ~/.local/lib/omarchy-arctis/headsetcontrol
```

The kernel can report battery without extra permissions. Reading the wheel and changing headset settings require HID access. The supplied `70-omarchy-arctis.rules` grants the active local seat access only to interface 03 of this USB ID. Install once:

```sh
sudo install -m644 70-omarchy-arctis.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

Reconnect the receiver, or trigger a change event for its control hidraw node to apply without reconnecting. The plugin never runs as root.

## Install locally

Copy this directory to `~/.config/omarchy/plugins/acrogenesis.arctis`, then:

```sh
omarchy plugin validate ~/.config/omarchy/plugins/acrogenesis.arctis
omarchy-shell shell rescanPlugins
omarchy plugin enable acrogenesis.arctis --section right --before omarchy.audio
```

Click the headphones icon and choose **Use Arctis Game as default output**. Existing headset playback moves to Game (communication apps to Chat). You can also select Arctis Chat directly inside voice apps. Application assignments and volume trims are stored in `~/.config/omarchy/arctis/config.json`.

The shell owns one backend process for all monitors. It polls headset status four times per second while connected, and audio/app state every two seconds. When disabled, it moves virtual playback back to the physical headset, restores the previous default if a virtual output is still selected, and removes its virtual outputs. When enabled again it resumes the Game default if previously requested and either the physical headset or saved alternate output is selected. Selecting another physical output while running is respected. Reconnection recreates the virtual outputs without restarting PipeWire.

## Diagnostics and tests

```sh
omarchy-shell acrogenesis.arctis status
python3 arctis.py --command '{"action":"status"}'
python3 -m unittest discover -s tests -v
node tests/test_panel.js # optional development check for dropdown label safety
```

The user-only runtime socket is `$XDG_RUNTIME_DIR/omarchy-arctis.sock`; it accepts bounded JSON commands. No audio is recorded. A failed HID query shows an error and unknown wheel position; battery can still come from the kernel. It never turns a failed read into a 0% battery reading.

The USB receiver stays present when the headset is off. An explicit offline response clears the displayed battery and wheel state. The kernel's cached capacity is ignored when its power status is Unknown; receiver presence alone never means the headset is on.

An unplugged USB receiver is shown as **Receiver disconnected**, separately from an off headset or an unavailable status query. The selected alternate output remains usable with the receiver unplugged. USB presence is checked independently of the headset's audio profile and HID permissions.

The compact main panel shows status, Game/Chat volume rows and a **When headset is off** dropdown. While the alternate output is active, an **Output** volume row controls that device's actual volume independently of Game/Chat. It follows volume changes made in other mixers; PipeWire owns the device volume. **Applications** expands app routing; **More settings** expands wheel-following, sidetone, inactivity and battery alerts. These sections fold again when you close the panel; saved settings remain unchanged.

Use **When headset is off** to choose your speakers or monitor output. Both virtual outputs route there while the headset is off, preserving application assignments and independent volume trims. The hardware wheel does not attenuate the alternate output. Turn the headset on to resume hardware mixing. A transient unknown power reading keeps an active alternate route; an unavailable selected output is reported without choosing a different device. **Don't switch outputs** disables this behavior. The normal system default can remain Arctis Game; its actual playback destination changes. Disabling the plugin while an alternate output is active moves playback to that output.

## Next ideas

- ChatMix on-screen display while turning the wheel in games.
- Per-game profiles and automatic app assignments from Steam game identity.
- Separate music/media bus and optional voice ducking.
- Software EQ presets (flat/music/footsteps), limiter and microphone noise suppression through PipeWire or EasyEffects. Hardware EQ on this updated firmware needs separate validation before exposing it.
- Connection notifications when the headset turns on or off.
- Battery history and a runtime estimate once enough discharge samples exist.

Firmware updates remain the responsibility of SteelSeries GG; this plugin does not flash firmware.
