#!/usr/bin/env python3
"""Render the real QML panel with isolated sample data; no live audio changes."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--frames', type=int, default=900)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    shell = Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy')) / 'shell'
    frames = args.output.resolve()
    frames.mkdir(parents=True, exist_ok=True)
    rig = frames.parent / 'qml-preview'
    rig.mkdir(parents=True, exist_ok=True)
    commons = rig / 'Commons'
    if not commons.exists():
        commons.symlink_to(shell / 'Commons', target_is_directory=True)
    ui = rig / 'Ui'
    ui.mkdir(exist_ok=True)
    for file in (shell / 'Ui').iterdir():
        target = ui / file.name
        if file.name == 'KeyboardPanel.qml':
            target.write_text((source / 'promo/KeyboardPanel.qml').read_text())
        elif not target.exists():
            target.symlink_to(file)
    text = (source / 'promo/preview.qml.in').read_text()
    text = text.replace('@PLUGIN@', json.dumps(source.as_uri()))
    text = text.replace('@COUNT@', str(args.frames)).replace('@OUTPUT@', json.dumps(str(frames)))
    (rig / 'shell.qml').write_text(text)
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software'}
    subprocess.run(['quickshell', '-p', str(rig)], env=env, check=True, timeout=240)
    if len(list(frames.glob('[0-9]*.png'))) != args.frames:
        raise SystemExit('Incomplete frame capture')
    print(f'{args.frames} QML frames saved to {frames}')


if __name__ == '__main__':
    main()
