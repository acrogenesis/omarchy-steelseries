#!/usr/bin/env python3
"""Render two 30-second promos from isolated captures of the real plugin UI."""
import argparse
from functools import lru_cache
import math
from pathlib import Path
import subprocess
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS, SECONDS = 30, 30
INK = (13, 18, 19)
WHITE = (234, 239, 229)
LIME = (198, 247, 119)
MUTED = (137, 151, 144)
FONT = '/usr/share/fonts/noto/NotoSans-Bold.ttf'
REGULAR = '/usr/share/fonts/noto/NotoSans-Regular.ttf'
MONO = '/usr/share/fonts/TTF/CaskaydiaMonoNerdFont-Regular.ttf'
SCENES = [
    (0, 3, 'ARCTIS / OMARCHY', ['Game. Chat.', 'In balance.'], 'Your Nova 7X. At home on Linux.'),
    (3, 9, '01 / HARDWARE CHATMIX', ['One wheel.', 'Two channels.'], 'Keep the game. Bring in your team.'),
    (9, 15, '02 / YOUR SETTINGS', ['Make it', 'sound right.'], 'Live volume. Sidetone. Auto-off.'),
    (15, 22, '03 / AUTOMATIC OUTPUT', ['Headset off?', 'Keep listening.'], 'Switch to speakers. Return automatically.'),
    (22, 26, '04 / NATIVE CONTROL', ['Small panel.', 'Full control.'], 'Battery status. Per-app routing.'),
    (26, 30, 'OPEN SOURCE / NOVA 7X', ['ARCTIS', 'for Omarchy.'], 'Your headset. Your desktop.'),
]


@lru_cache(maxsize=100)
def font(size, face=FONT):
    return ImageFont.truetype(face, int(size))


def ease(value):
    value = max(0, min(1, value))
    return 1 - (1 - value) ** 3


def text(draw, xy, value, size, fill=WHITE, face=FONT, **kwargs):
    draw.text(xy, value, font=font(size, face), fill=fill, **kwargs)


def headphones(draw, x, y, size, color=LIME, width=3):
    draw.arc((x, y, x + size, y + size), 180, 360, fill=color, width=width)
    draw.rounded_rectangle((x - width, y + size * .45, x + size * .17, y + size * .85), radius=4, fill=color)
    draw.rounded_rectangle((x + size * .83, y + size * .45, x + size + width, y + size * .85), radius=4, fill=color)


def wrap(draw, value, size, width):
    lines, line = [], ''
    for word in value.split():
        candidate = (line + ' ' + word).strip()
        if draw.textlength(candidate, font=font(size, REGULAR)) > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


@lru_cache(maxsize=2)
def backdrop(width, height):
    y, x = np.mgrid[0:height, 0:width]
    glow = np.exp(-(((x / width - .8) / .6) ** 2 + ((y / height - .5) / .65) ** 2) * 2)
    rng = np.random.default_rng(19)
    noise = rng.normal(0, .65, (height, width))
    rgb = np.stack([INK[c] + glow * (4, 10, 6)[c] + noise for c in range(3)], axis=2)
    image = Image.fromarray(np.clip(rgb, 0, 255).astype('uint8'))
    draw = ImageDraw.Draw(image)
    for xx in range(24, width, 48):
        for yy in range(24, height, 48):
            draw.point((xx, yy), fill=(34, 43, 38))
    return image


@lru_cache(maxsize=128)
def shadow(width, height):
    image = Image.new('RGBA', (width + 120, height + 120))
    ImageDraw.Draw(image).rounded_rectangle((60, 60, width + 60, height + 60), radius=18, fill=(0, 0, 0, 155))
    return image.filter(ImageFilter.GaussianBlur(26))


class Film:
    def __init__(self, frames, vertical):
        self.frames = frames
        self.vertical = vertical
        self.width, self.height = (1080, 1920) if vertical else (1920, 1080)
        self.card_height = 0

    def frame(self, index):
        t = index / FPS
        w, h = self.width, self.height
        vertical = self.vertical
        image = backdrop(w, h).copy().convert('RGBA')
        draw = ImageDraw.Draw(image)
        margin = 72 if vertical else 104
        headphones(draw, margin, 80, 42)
        text(draw, (margin + 64, 72), 'ARCTIS', 32, face=MONO)
        text(draw, (w - margin, 88), 'OMARCHY PLUGIN', 20, fill=MUTED, face=MONO, anchor='ra')
        draw.line((margin, 152, w - margin, 152), fill=(52, 66, 56), width=1)
        scene_index, scene = next((i, s) for i, s in enumerate(SCENES) if s[0] <= t < s[1])
        start, end, eyebrow, title, subtitle = scene
        since = t - start
        alpha = ease(since / .4) * min(1, max(0, (end - t) / .18))
        copy = Image.new('RGBA', (w, h))
        labels = ImageDraw.Draw(copy)
        tx, ty = margin, 267 if vertical else 286
        text(labels, (tx, ty - 62), eyebrow, 20, fill=LIME, face=MONO)
        hero_size = 101 if vertical else 96
        for i, line in enumerate(title):
            lift = int(32 * (1 - ease((since - i * .055) / .45)))
            text(labels, (tx, ty + i * int(hero_size * 1.18) + lift), line,
                 hero_size, fill=WHITE if i == 0 else LIME)
        sy = ty + int(hero_size * 2.6)
        for i, line in enumerate(wrap(labels, subtitle, 28, 900 if vertical else 850)):
            text(labels, (tx + 3, sy + i * 42), line, 28, fill=WHITE, face=REGULAR)

        if not vertical and scene_index == 1:
            phase = math.sin((t - 3) * math.pi / 3)
            self.levels(labels, tx, 720, 700, min(1, 1 + min(0, phase)), min(1, 1 - max(0, phase)))
        elif not vertical and scene_index == 2:
            self.feature(labels, tx, 738, 'LIVE VOLUME', 'Changes while you drag')
            self.feature(labels, tx, 837, 'SIDETONE + IDLE TIMER', 'Set it once. Keep it yours.')
        elif not vertical and scene_index == 3:
            self.switch(labels, tx, 754, 690, since)
        elif not vertical and scene_index == 4:
            self.feature(labels, tx, 740, 'GAME / CHAT', 'Remember each app\u2019s output')
            self.feature(labels, tx, 837, 'BATTERY AT A GLANCE', 'Power and charge status in your bar')
        elif scene_index == 5:
            cta_y = 1610 if vertical else 744
            text(labels, (tx, cta_y), 'EXPLORE THE SOURCE', 20, fill=LIME, face=MONO)
            url = ['github.com/acrogenesis/', 'omarchy-arctis'] if vertical else ['github.com/acrogenesis/omarchy-arctis']
            for i, line in enumerate(url):
                text(labels, (tx, cta_y + 42 + i * 39), line, 28 if vertical else 25, face=MONO)
        if vertical and scene_index == 1:
            self.levels(labels, tx, 1530, w - margin * 2, min(1, 1 + min(0, math.sin((t-3)*math.pi/3))),
                        min(1, 1 - max(0, math.sin((t-3)*math.pi/3))))
        if vertical and scene_index == 3:
            self.switch(labels, tx, 1545, w - margin * 2, since)
        if vertical and scene_index == 5:
            headphones(labels, w / 2 - 135, 910, 270, width=15)
            text(labels, (w / 2, 1260), 'NATIVE. LOCAL. YOURS.', 25, fill=MUTED, face=MONO, anchor='ma')
        if alpha < 1:
            copy.putalpha(copy.getchannel('A').point(lambda p: int(p * alpha)))
        image.alpha_composite(copy)

        # Actual plugin component capture, rendered at 2x resolution.
        ui = Image.open(self.frames / f'{index:05}.png').convert('RGBA')
        target_width = 860 if vertical else 640
        cap = 960 if vertical else 790
        target_width = min(target_width, int((cap - 46) * ui.width / ui.height))
        target_height = int(ui.height * target_width / ui.width)
        if not self.card_height:
            self.card_height = target_height
        self.card_height += (target_height - self.card_height) * .28
        ch = round(self.card_height)
        pw, ph = target_width + 24, ch + 48
        x = (w - pw) // 2 if vertical else w - margin - pw
        y = min(760, 1695 - ph) if vertical else int(560 - ph / 2)
        # UI disappears behind the final source card in the portrait cut.
        card_alpha = 1 if not vertical or t < 26 else max(0, 1 - (t - 26) / .6)
        card = Image.new('RGBA', (pw, ph))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=16, fill=(24, 31, 32), outline=(72, 92, 72), width=1)
        cd.ellipse((18, 17, 25, 24), fill=LIME)
        text(cd, (37, 11), 'PLUGIN PREVIEW', 15, fill=MUTED, face=MONO)
        text(cd, (pw - 18, 13), 'DEMO', 13, fill=MUTED, face=MONO, anchor='ra')
        ui = ui.resize((target_width, target_height), Image.Resampling.LANCZOS)
        if target_height > ch:
            ui = ui.crop((0, 0, target_width, ch))
        card.alpha_composite(ui, (12, 36))
        reveal = ease(t / .8) * card_alpha
        if reveal < 1:
            card.putalpha(card.getchannel('A').point(lambda p: int(p * reveal)))
        if reveal > 0:
            image.alpha_composite(shadow(pw, ph), (x - 60, y - 45))
            image.alpha_composite(card, (x, y + int((1 - ease(t / .8)) * 36)))

        draw = ImageDraw.Draw(image)
        footer_y = h - 96
        text(draw, (margin, footer_y), 'ARCTIS NOVA 7X  /  LINUX + PIPEWIRE', 17, fill=MUTED, face=MONO)
        text(draw, (w - margin, footer_y), f'{scene_index + 1:02} / 06', 18, fill=LIME, face=MONO, anchor='ra')
        draw.line((margin, h - 48, w - margin, h - 48), fill=(43, 55, 46), width=2)
        draw.line((margin, h - 48, margin + int((w - 2 * margin) * t / SECONDS), h - 48), fill=LIME, width=3)
        # Whole-film bookends soften playback without hiding the final CTA.
        fade = min(1, t / .3, (SECONDS - t) / .4)
        if fade < 1:
            image = Image.blend(Image.new('RGBA', (w, h), INK + (255,)), image, max(0, fade))
        return image.convert('RGB')

    @staticmethod
    def feature(draw, x, y, label, description):
        draw.line((x, y + 10, x + 34, y + 10), fill=LIME, width=3)
        text(draw, (x + 52, y - 3), label, 21, fill=LIME, face=MONO)
        text(draw, (x + 52, y + 32), description, 24, fill=MUTED, face=REGULAR)

    @staticmethod
    def levels(draw, x, y, width, game, chat):
        for i, (name, value) in enumerate((('GAME', game), ('CHAT', chat))):
            yy = y + i * 66
            text(draw, (x, yy), name, 19, fill=MUTED, face=MONO)
            left = x + 90
            right = x + width - 60
            draw.rounded_rectangle((left, yy + 9, right, yy + 18), radius=4, fill=(49, 61, 51))
            end = left + (right - left) * value
            draw.rounded_rectangle((left, yy + 9, max(left + 1, end), yy + 18), radius=4, fill=LIME)
            text(draw, (x + width, yy), f'{round(value*100)}%', 19, fill=WHITE, face=MONO, anchor='ra')

    @staticmethod
    def switch(draw, x, y, width, elapsed):
        headphones(draw, x + 18, y, 50, color=MUTED)
        left, right = x + 110, x + width - 110
        draw.line((left, y + 23, right, y + 23), fill=(55, 71, 57), width=2)
        progress = min(1, elapsed / 1.1)
        draw.line((left, y + 23, left + (right-left) * progress, y + 23), fill=LIME, width=3)
        xx = x + width - 65
        draw.rounded_rectangle((xx, y + 2, xx + 58, y + 40), radius=3, outline=LIME, width=3)
        draw.line((xx + 29, y + 41, xx + 29, y + 51), fill=LIME, width=3)
        draw.line((xx + 13, y + 53, xx + 45, y + 53), fill=LIME, width=3)
        text(draw, (x, y + 78), 'HEADSET', 18, fill=MUTED, face=MONO)
        text(draw, (x + width, y + 78), 'SPEAKERS', 18, fill=LIME, face=MONO, anchor='ra')


def music(output):
    rate = 48000
    audio = np.zeros((rate * SECONDS, 2), dtype=np.float64)
    rng = np.random.default_rng(120)
    chords = [(57, 60, 64, 67), (53, 57, 60, 64), (48, 52, 55, 59), (55, 59, 62, 69)]
    def add(at, sound, volume=1, pan=0):
        start = round(at * rate)
        count = min(len(sound), len(audio) - start)
        if count > 0:
            audio[start:start+count, 0] += sound[:count] * volume * math.sqrt((1-pan)/2)
            audio[start:start+count, 1] += sound[:count] * volume * math.sqrt((1+pan)/2)
    def hz(note):
        return 440 * 2 ** ((note - 69) / 12)
    for at in np.arange(0, SECONDS, 4):
        chord = chords[int(at / 4) % len(chords)]
        tt = np.arange(int(rate * 4.2)) / rate
        envelope = np.minimum(1, tt / .18) * np.minimum(1, (4.2 - tt) / .65)
        for i, note in enumerate(chord):
            sound = np.sin(2*np.pi*hz(note)*tt + .07*np.sin(2*np.pi*.4*tt)) * envelope
            add(at, sound, .025, (i-1.5)/3)
    for beat, at in enumerate(np.arange(0, SECONDS, .5)):
        chord = chords[int(at / 4) % 4]
        tt = np.arange(int(rate * .42)) / rate
        bass = np.sin(2*np.pi*hz(chord[0]-24)*tt) * np.exp(-tt*7) * np.minimum(1, tt/.012)
        add(at, bass, .15)
        if beat % 2 == 0:
            kick = np.sin(2*np.pi*(47*tt+2.8*(1-np.exp(-tt*27)))) * np.exp(-tt*18)
            add(at, kick, .22)
        note = chord[(beat * 3) % 4] + 12
        pluck = (np.sin(2*np.pi*hz(note)*tt)+.22*np.sin(4*np.pi*hz(note)*tt)) * np.exp(-tt*12) * np.minimum(1, tt/.006)
        add(at + .25, pluck, .035, .45 if beat % 2 else -.45)
        noise = rng.normal(0, 1, int(rate*.055))
        noise = np.diff(noise, prepend=0) * np.exp(-np.arange(len(noise))/rate*90)
        add(at + .25, noise, .012, -.2 if beat % 2 else .2)
    for at in (3, 9, 15, 22, 26):
        tt = np.arange(int(rate*.32))/rate
        swoosh = rng.normal(0,1,len(tt)) * np.sin(np.pi*tt/.32)**2
        add(at-.18, swoosh, .012)
    tt = np.arange(len(audio))/rate
    audio *= (np.minimum(1, tt/.4)*np.minimum(1, (SECONDS-tt)/1.2))[:,None]
    audio = np.tanh(audio * 1.7)
    audio *= .75 / max(.75, np.max(np.abs(audio)))
    with wave.open(str(output), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((audio*32767).astype('<i2').tobytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('frames', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--format', choices=('both', 'landscape', 'vertical'), default='both')
    parser.add_argument('--stills', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    formats = ('landscape', 'vertical') if args.format == 'both' else (args.format,)
    if not args.stills:
        music(args.output / 'original-score.wav')
    for name in formats:
        film = Film(args.frames, name == 'vertical')
        if args.stills:
            for second in (1.8, 6.3, 13.1, 18.5, 23.5, 28):
                film.card_height = 0
                film.frame(round(second*FPS)).save(args.output / f'{name}-{second:.1f}.png')
            print(f'{name} storyboard saved', flush=True)
            continue
        target = args.output / f'arctis-promo-{name}-30s.mp4'
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                   '-s', f'{film.width}x{film.height}', '-r', str(FPS), '-i', 'pipe:0',
                   '-i', str(args.output / 'original-score.wav'), '-map', '0:v', '-map', '1:a',
                   '-vf', 'scale=out_color_matrix=bt709:out_range=tv',
                   '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-threads', '6', '-pix_fmt', 'yuv420p',
                   '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
                   '-c:a', 'aac', '-b:a', '192k', '-af', 'loudnorm=I=-18:TP=-1.5:LRA=7', '-ar', '48000',
                   '-t', str(SECONDS), '-movflags', '+faststart', str(target)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            for frame in range(FPS*SECONDS):
                process.stdin.write(film.frame(frame).tobytes())
                if frame % 150 == 0:
                    print(f'{name}: {frame}/{FPS*SECONDS}', flush=True)
        finally:
            process.stdin.close()
        if process.wait() != 0:
            raise SystemExit('Video encoder failed')
        print(f'Created {target}', flush=True)


if __name__ == '__main__':
    main()
