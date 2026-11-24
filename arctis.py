#!/usr/bin/env python3
"""SteelSeries Headsets backend for Omarchy."""
import argparse
from collections import Counter
import ctypes
import fcntl
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time

GAME, CHAT = "arctis_game", "arctis_chat"
CHANNELS = (GAME, CHAT)
PROJECT = "omarchy-steelseries"
STEELSERIES = "1038"
NOVA_7X = "22a5"
BATTERY = "CAP_BATTERY_STATUS"
WHEEL = "CAP_CHATMIX_STATUS"
SETTINGS = {"sidetone": "CAP_SIDETONE", "sleep": "CAP_INACTIVE_TIME"}
# Some bases expose their audio and controls under different USB product IDs.
BASE_AUDIO = {"1290": "1294", "1280": "1282"}
# The PipeWire gaming headset profiles put Chat on PCM 0 and Game on PCM 1.
SPLIT_AUDIO = {"1260", "12ad", "1294", "12c4", "1252", "1282"}
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy/arctis/config.json"
SOCKET = RUNTIME / f"{PROJECT}.sock"


def terminate_with_parent(expected_parent=None):
    """Linux: a killed shell proxy must still give the backend time to clean up."""
    parent = expected_parent if expected_parent is not None else os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Cannot set parent-death signal")
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGTERM)


def run(*args, json_output=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:300] or f"{args[0]} failed")
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def gains(level):
    """Center = both full; toward an endpoint attenuates the other channel."""
    if not isinstance(level, (float, int)) or not math.isfinite(level) or not 0 <= level <= 128:
        raise ValueError("Invalid ChatMix level")
    return min(1, (128 - level) / 64), min(1, level / 64)


def app_key(props):
    for name in ("application.id", "application.process.binary", "application.name"):
        value = props.get(name)
        if isinstance(value, str) and value:
            return value[:200]
    return ""


def route_for(props, rules):
    rule = rules.get(app_key(props))
    if rule in CHANNELS:
        return rule
    app = app_key(props).lower()
    if props.get("media.role", "").lower() in ("communication", "phone") or any(
        name in app for name in ("discord", "vesktop", "zoom", "teams-for-linux")
    ):
        return CHAT
    return GAME


def usb_id(value):
    value = str(value).lower().removeprefix("0x")
    return value if re.fullmatch(r"[0-9a-f]{4}", value) else ""


def usb_products(directory=Path("/sys/bus/usb/devices")):
    products = Counter()
    for device in directory.iterdir():
        try:
            if (device / "idVendor").read_text().strip().lower() == STEELSERIES:
                product = usb_id((device / "idProduct").read_text().strip())
                if product:
                    products[product] += 1
        except FileNotFoundError:
            pass  # USB interfaces have no idVendor/idProduct files.
    return products


def read_power(product):
    for path in Path("/sys/class/power_supply").glob(f"steelseries*{STEELSERIES}:{product.upper()}.*"):
        try:
            present = (path / "present").read_text().strip() == "1"
            capacity = int((path / "capacity").read_text())
            status = (path / "status").read_text().strip()
            # The receiver remains present and capacity stays cached when the
            # headset is off. Only an active power status validates capacity.
            online = present and status in ("Charging", "Discharging", "Full", "Not charging")
            return {"online": online if online or not present else None,
                    "battery": capacity if online and 0 <= capacity <= 100 else None,
                    "charging": online and status == "Charging"}
        except (OSError, ValueError):
            pass
    return {"online": None, "battery": None, "charging": False}


def device_product(device):
    return usb_id(device.get("id_product")) if usb_id(device.get("id_vendor")) == STEELSERIES else ""


def reported_devices(data):
    devices = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices, list):
        raise ValueError("HeadsetControl returned an invalid device list")
    by_product = {}
    for device in devices:
        if isinstance(device, dict) and (product := device_product(device)):
            by_product[product] = device
    return by_product


def sink_product(sink):
    props = sink.get("properties", {})
    vendor = usb_id(props.get("device.vendor.id"))
    product = usb_id(props.get("device.product.id"))
    if vendor and vendor != STEELSERIES:
        return ""
    if vendor == STEELSERIES and product:
        return product
    # ALSA supplies this even when a PipeWire profile omits USB properties.
    for component in str(props.get("alsa.components", "")).lower().split():
        if component.startswith(f"usb{STEELSERIES}:"):
            return usb_id(component.split(":", 1)[1])
    return ""


def headset_sinks(sinks, product, products):
    if not product or products[product] != 1:
        return []
    audio_products = {product}
    companion = BASE_AUDIO.get(product)
    if companion and products[companion] == 1:
        audio_products.add(companion)
    return [sink for sink in sinks if sink["name"].startswith("alsa_output.") and sink_product(sink) in audio_products]


def headset_destinations(sinks):
    game, chat = "", ""
    for sink in sinks:
        props = sink.get("properties", {})
        profile = props.get("device.profile.name", "")
        product = sink_product(sink)
        pcm = str(props.get("alsa.device", props.get("api.alsa.pcm.device", "")))
        if profile in ("stereo-game", "analog-game") or product in SPLIT_AUDIO and pcm == "1":
            game = sink["name"]
        elif profile in ("mono-chat", "stereo-chat", "analog-chat") or product in SPLIT_AUDIO and pcm == "0":
            chat = sink["name"]
    if game and chat:
        return {GAME: game, CHAT: chat}
    main = game or (sinks[0]["name"] if sinks else "")
    return {GAME: main, CHAT: main}


def other_outputs(sinks, headset_outputs):
    outputs = []
    for sink in sinks:
        name = sink['name']
        if name in CHANNELS or name in headset_outputs:
            continue
        if str(sink.get('properties', {}).get('node.virtual', '')).lower() in ('true', '1'):
            continue
        active = sink.get('active_port')
        port = next((p for p in sink.get('ports', []) if p.get('name') == active), {})
        available = port.get('availability') not in ('not available', 'no')
        outputs.append({'name': name, 'label': str(sink.get('description') or name)[:180], 'available': available})
    return outputs[:32]


def output_target(online, physical, fallback, available, previous):
    if online is False and fallback and fallback in available:
        return fallback
    # A transient failed power query must not interrupt active fallback audio.
    if online is None and previous == fallback and fallback and fallback in available:
        return fallback
    return physical


def sink_volume(sink):
    values = [channel['value'] / 65536 * 100 for channel in sink.get('volume', {}).values()]
    return round(sum(values) / len(values)) if values else None


def load_config():
    defaults = {"game": 100, "chat": 100, "followWheel": True, "rules": {}, "alerts": True,
                "useDefault": False, "sidetone": None, "sleep": None, "headsetSettings": {}, "fallback": ""}
    try:
        raw = json.loads(CONFIG.read_text())
        for key in ("game", "chat"):
            if type(raw.get(key)) is int and 0 <= raw[key] <= 100:
                defaults[key] = raw[key]
        for key in ("followWheel", "alerts", "useDefault"):
            if type(raw.get(key)) is bool:
                defaults[key] = raw[key]
        for key, maximum in (("sidetone", 3), ("sleep", 90)):
            if type(raw.get(key)) is int and 0 <= raw[key] <= maximum:
                defaults[key] = raw[key]
        choices = raw.get("headsetSettings")
        if isinstance(choices, dict):
            for product, settings in choices.items():
                if usb_id(product) == product and product and isinstance(settings, dict):
                    defaults["headsetSettings"][product] = {
                        key: settings[key] for key, limit in (("sidetone", 3), ("sleep", 90))
                        if type(settings.get(key)) is int and 0 <= settings[key] <= limit
                    }
        if isinstance(raw.get('fallback'), str) and len(raw['fallback']) <= 256:
            defaults['fallback'] = raw['fallback']
        defaults["rules"] = {k[:200]: v for k, v in raw.get("rules", {}).items()
                             if isinstance(k, str) and v in CHANNELS}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return defaults


class Backend:
    def __init__(self):
        self.config = load_config()
        self.loopbacks = {}
        self.physical = ""
        self.headset_outputs = headset_destinations([])
        self.native_mix = False
        self.output_target = ""
        self.routed_target = ""
        self.outputs = []
        self.original_default = ""
        self.level = None
        self.applied = None
        self.playbacks = {}
        self.initialized_sinks = set()
        self.seen_streams = set()
        self.adopted = False
        self.apps = []
        self.audio = {}
        self.error = ""
        self.action_error = ""
        self.control_error = ""
        self.power = {"online": None, "battery": None, "charging": False}
        self.products = Counter()
        self.devices = {}
        self.device = {}
        self.product = ""
        self.capabilities = set()
        self.discover_at = 0
        self.receiver = None
        self.last_low = False
        self.last_graph = 0
        library = Path.home() / ".local/lib"
        bundled = library / PROJECT / "headsetcontrol"
        legacy = library / "omarchy-arctis/headsetcontrol"
        if not bundled.exists() and legacy.exists():
            bundled = legacy
        self.hsc = shutil.which("headsetcontrol") or str(bundled)
        self.stopping = False

    def save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, indent=2) + "\n")
        temporary.replace(CONFIG)

    def discover(self):
        products = usb_products()
        now = time.monotonic()
        if products != self.products or now >= self.discover_at:
            # HID enumeration does not open devices or write headset settings.
            if products:
                data = run(self.hsc, "-d", f"{STEELSERIES}:0000", "--caps", "-o", "json", json_output=True)
                self.devices = reported_devices(data)
            else:
                self.devices = {}
            self.products = products
            self.discover_at = now + 5
        eligible = sorted(pid for pid in self.devices if products[pid] == 1)
        if eligible:
            product = self.product if self.product in eligible else eligible[0]
            if self.product != product:
                self.product = product
                self.level = self.applied = None
                self.last_low = False
            self.device = self.devices[product]
            capabilities = self.device.get("capabilities", [])
            self.capabilities = {cap for cap in capabilities if isinstance(cap, str)} if isinstance(capabilities, list) else set()
        self.receiver = bool(self.product and self.product in eligible)
        if not self.receiver and any(products[pid] > 1 for pid in self.devices):
            raise ValueError("Connect only one headset of each USB model")

    def headset(self):
        self.control_error = ""
        try:
            self.discover()
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            self.receiver = None
            self.power = {"online": None, "battery": None, "charging": False}
            self.level = None
            self.control_error = str(exc)[:300]
            return
        if self.receiver is False:
            self.power = {'online': False, 'battery': None, 'charging': False}
            self.level = None
            self.last_low = False
            return
        self.power = read_power(self.product)
        self.level = None
        flags = [flag for capability, flag in ((BATTERY, "-b"), (WHEEL, "-m")) if capability in self.capabilities]
        if not flags:
            return
        try:
            data = run(self.hsc, "-d", f"{STEELSERIES}:{self.product}", *flags, "-o", "json", json_output=True)
            device = reported_devices(data).get(self.product)
            if not device:
                self.control_error = "Headset receiver not found"
                return
            errors = device.get("errors") or {}
            battery = device.get("battery") or {}
            status = battery.get("status")
            if status in ("BATTERY_AVAILABLE", "BATTERY_CHARGING"):
                level = battery.get("level")
                valid_level = type(level) is int and 0 <= level <= 100
                if valid_level or status == "BATTERY_CHARGING":
                    self.power = {"online": True, "battery": level if valid_level else None,
                                  "charging": status == "BATTERY_CHARGING"}
            elif status == "BATTERY_UNAVAILABLE":
                error = str(errors.get("battery", "")).strip().casefold()
                # HeadsetControl serializes DeviceOffline using its generic
                # message, not the device implementation's "not connected" detail.
                if error.startswith("device is offline") or "not connected" in error:
                    self.power = {"online": False, "battery": None, "charging": False}
            if self.power["online"] is not False:
                if self.power["online"] is None and errors.get("battery"):
                    self.control_error = str(errors["battery"])[:300]
                level = device.get("chatmix")
                if WHEEL in self.capabilities and (self.power["online"] is True or BATTERY not in self.capabilities):
                    if type(level) is int and 0 <= level <= 128:
                        self.level = level
                    else:
                        self.control_error = str(errors.get("chatmix", "Wheel status unavailable"))[:300]
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            self.level = None
            self.control_error = str(exc)[:300]
        low = self.power["online"] is True and self.power["battery"] is not None and self.power["battery"] <= 20
        if low and not self.last_low and self.config["alerts"] and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "--app-name=SteelSeries Headsets", "--icon=audio-headphones",
                              "Headset battery low", f"Battery: {self.power['battery']}%"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.last_low = low

    def destination(self, channel):
        if self.output_target == self.physical:
            return self.headset_outputs[channel] or self.physical
        return self.output_target

    def start_loopbacks(self):
        for name, label in ((GAME, "Game"), (CHAT, "Chat")):
            process = self.loopbacks.get(name)
            if process is not None and process.poll() is None:
                continue
            capture = {"media.class": "Audio/Sink", "node.name": name,
                       "node.description": f"SteelSeries {label}", "node.virtual": True,
                       "priority.session": 0, "application.id": PROJECT}
            playback = {"node.name": f"{name}_playback", "node.description": f"SteelSeries {label} playback",
                        "node.dont-fallback": True, "node.passive": True,
                        "application.id": f"{PROJECT}.{label.lower()}"}
            backend_pid = os.getpid()
            self.loopbacks[name] = subprocess.Popen([
                "pw-loopback", "-n", f"omarchy_{name}", "-c", "2", "-m", "[ FL FR ]",
                "-P", self.destination(name), "-i", json.dumps(capture), "-o", json.dumps(playback)],
                stdout=subprocess.DEVNULL,
                preexec_fn=lambda: terminate_with_parent(backend_pid))
            self.applied = None

    def stop_loopbacks(self):
        for process in self.loopbacks.values():
            if process.poll() is None:
                process.terminate()
        for process in self.loopbacks.values():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.loopbacks.clear()
        self.applied = None
        self.playbacks = {}
        self.initialized_sinks.clear()
        self.seen_streams.clear()

    def graph(self):
        self.routed_target = ""
        sinks = run("pactl", "-f", "json", "list", "sinks", json_output=True)
        matches = headset_sinks(sinks, self.product, self.products) if self.receiver is not False else []
        self.headset_outputs = headset_destinations(matches)
        physical = self.headset_outputs[GAME]
        self.native_mix = physical != self.headset_outputs[CHAT]
        if physical != self.physical:
            self.physical = physical
            self.adopted = False
        self.audio = {s["name"]: s for s in sinks}
        headset_outputs = {s["name"] for s in matches}
        self.outputs = other_outputs(sinks, headset_outputs)
        available = {o['name'] for o in self.outputs if o['available']}
        power = self.power['online']
        # The receiver can enumerate before PipeWire creates its sink.
        if power is True and not physical:
            power = None
        self.output_target = output_target(power, physical, self.config['fallback'], available, self.output_target)
        if self.output_target:
            self.start_loopbacks()
        elif self.loopbacks:
            self.cleanup()
            self.audio = {name: sink for name, sink in self.audio.items() if name not in CHANNELS}
        self.default_sink = run("pactl", "get-default-sink")
        if not self.adopted and all(n in self.audio for n in CHANNELS):
            self.adopted = True
            resumable = headset_outputs | {self.output_target}
            if self.config['fallback'] in available:
                resumable.add(self.config['fallback'])
            if self.config["useDefault"] and self.default_sink in resumable:
                self.original_default = self.default_sink
                run("pactl", "set-default-sink", GAME)
                self.default_sink = GAME
        config_changed = False
        for name, key in ((GAME, "game"), (CHAT, "chat")):
            if name not in self.audio:
                continue
            if name not in self.initialized_sinks:
                run("pactl", "set-sink-volume", name, f"{self.config[key]}%")
                self.initialized_sinks.add(name)
            else:
                # Follow volume keys and other mixers. Wheel attenuation lives
                # on the playback stream, so it cannot overwrite these trims.
                values = [v["value"] / 65536 * 100 for v in self.audio[name].get("volume", {}).values()]
                if values:
                    volume = max(0, min(100, round(sum(values) / len(values))))
                    if volume != self.config[key]:
                        self.config[key] = volume
                        config_changed = True
        if config_changed:
            self.save()
        by_index = {s["index"]: s["name"] for s in sinks}
        streams = run("pactl", "-f", "json", "list", "sink-inputs", json_output=True)
        apps = []
        playbacks = {}
        routed_channels = set()
        seen = set()
        for stream in streams:
            props = stream.get("properties", {})
            own = props.get("application.id") in (f"{PROJECT}.game", f"{PROJECT}.chat")
            if own:
                for channel in CHANNELS:
                    if props.get("node.name") == f"{channel}_playback":
                        playbacks[channel] = stream["index"]
                        current_output = by_index.get(stream.get('sink'))
                        target = self.destination(channel)
                        if target in self.audio and current_output and current_output != target:
                            run('pactl', 'move-sink-input', str(stream['index']), target)
                            current_output = target
                        if current_output and current_output == target:
                            routed_channels.add(channel)
                continue
            if props.get("application.process.binary") == "pw-loopback":
                continue
            current = by_index.get(stream.get("sink"), "")
            if current not in CHANNELS and current not in headset_outputs:
                continue
            identity = (stream["index"], app_key(props))
            seen.add(identity)
            explicit = app_key(props) in self.config["rules"]
            desired = route_for(props, self.config["rules"])
            if self.native_mix and not explicit and current == self.headset_outputs[CHAT]:
                desired = CHAT
            if current in CHANNELS and not explicit:
                # Respect an app's chosen output. Only classify a new stream
                # arriving on Game (the general default) as communication.
                if current == CHAT or identity in self.seen_streams:
                    desired = current
            # Only take over physical playback once Game/Chat is the default,
            # or when this app has an explicit saved assignment.
            default = getattr(self, "default_sink", "")
            if desired in self.audio and (current in CHANNELS or default in CHANNELS or app_key(props) in self.config["rules"]):
                if desired != current:
                    run("pactl", "move-sink-input", str(stream["index"]), desired)
                    current = desired
            apps.append({"id": stream["index"], "key": app_key(props),
                         "name": str(props.get("application.name", "Audio"))[:100],
                         "channel": current, "saved": app_key(props) in self.config["rules"]})
        self.apps = apps[:32]
        self.seen_streams = seen
        if playbacks != self.playbacks:
            self.applied = None
        self.playbacks = playbacks
        if all(n in routed_channels for n in CHANNELS) and self.output_target in self.audio:
            self.routed_target = self.output_target

    def apply_volumes(self):
        if not all(n in self.playbacks for n in CHANNELS):
            return
        follow_wheel = (self.config["followWheel"] and WHEEL in self.capabilities
                        and not self.native_mix and self.output_target == self.physical)
        if follow_wheel and self.level is None and self.applied is not None:
            return  # A failed read must not suddenly lift the last attenuation.
        attenuation = gains(self.level) if follow_wheel and self.level is not None else (1, 1)
        volumes = tuple(round(100 * gain) for gain in attenuation)
        if volumes != self.applied:
            for name, volume in zip(CHANNELS, volumes):
                run("pactl", "set-sink-input-volume", str(self.playbacks[name]), f"{volume}%")
            self.applied = volumes

    def command(self, request):
        action = request.get("action")
        if action == "volume":
            key, value = request.get("channel"), request.get("value")
            if key not in ("game", "chat") or type(value) is not int or not 0 <= value <= 100:
                raise ValueError("Volume must be 0-100")
            self.config[key] = value
            run("pactl", "set-sink-volume", GAME if key == "game" else CHAT, f"{value}%")
            self.save()
        elif action in ("followWheel", "alerts"):
            if type(request.get("value")) is not bool:
                raise ValueError("Expected a boolean")
            self.config[action] = request["value"]
            self.save()
        elif action == 'fallback':
            name = request.get('value')
            if not isinstance(name, str) or (name and name not in {o['name'] for o in self.outputs if o['available']}):
                raise ValueError('Audio output is unavailable')
            self.config['fallback'] = name
            self.save()
            self.graph()
        elif action == 'fallbackVolume':
            value = request.get('value')
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError('Volume must be 0-100')
            name = self.config['fallback']
            if not name or request.get('output') != name:
                raise ValueError('The selected output changed')
            if self.power['online'] is True or self.routed_target != name or not any(
                o['name'] == name and o['available'] for o in self.outputs
            ):
                raise ValueError('Alternate output is not active')
            run('pactl', 'set-sink-volume', name, f'{value}%')
            self.graph()
        elif action == "default":
            if GAME not in self.audio:
                raise ValueError("Game output is not ready")
            previous = run("pactl", "get-default-sink")
            if previous not in CHANNELS:
                self.original_default = previous
            run("pactl", "set-default-sink", GAME)
            self.config["useDefault"] = True
            self.save()
            self.default_sink = GAME
            self.graph()
        elif action == "route":
            channel = request.get("channel")
            app = next((a for a in self.apps if a["id"] == request.get("id")), None)
            if channel not in CHANNELS or not app or not app["key"]:
                raise ValueError("Audio stream no longer available")
            run("pactl", "move-sink-input", str(app["id"]), channel)
            self.config["rules"][app["key"]] = channel
            self.save()
            self.graph()
        elif action == "forget":
            self.config["rules"] = {}
            self.save()
        elif action in ("sidetone", "sleep"):
            value = request.get("value")
            maximum = 3 if action == "sidetone" else 90
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("Setting out of range")
            if request.get("product") != self.product or not self.receiver or usb_products()[self.product] != 1:
                raise ValueError("The selected headset changed")
            if SETTINGS[action] not in self.capabilities or self.power["online"] is False:
                raise ValueError("Setting unavailable on this headset")
            flag = "--sidetone" if action == "sidetone" else "--inactive-time"
            value = (0, 43, 85, 128)[value] if action == "sidetone" else value
            data = run(self.hsc, "-d", f"{STEELSERIES}:{self.product}", flag, str(value), "-o", "json", json_output=True)
            device = reported_devices(data).get(self.product, {})
            if not device or device.get("errors") or device.get("status") != "success":
                raise RuntimeError(str(device.get("errors", "Setting could not be applied")))
            # Persist each setting only after the hardware command succeeds.
            # These are last-applied choices, not device readback.
            choices = self.config if self.product == NOVA_7X else self.config["headsetSettings"].setdefault(self.product, {})
            choices[action] = request["value"]
            self.save()
        elif action != "status":
            raise ValueError("Unknown action")
        self.apply_volumes()

    def status(self):
        fallback_available = any(o['name'] == self.config['fallback'] and o['available'] for o in self.outputs)
        choices = self.config if self.product == NOVA_7X else self.config["headsetSettings"].get(self.product, {})
        return {**self.power, "receiver": self.receiver, "wheel": self.level,
                "product": self.product, "headsetName": str(self.device.get("product") or self.device.get("device") or "")[:100],
                "supportsBattery": BATTERY in self.capabilities or self.power["battery"] is not None,
                "supportsWheel": WHEEL in self.capabilities and not self.native_mix,
                "supportsSidetone": SETTINGS["sidetone"] in self.capabilities,
                "supportsSleep": SETTINGS["sleep"] in self.capabilities,
                "wheelAvailable": self.level is not None and not self.native_mix, "physical": self.physical,
                "game": self.config["game"], "chat": self.config["chat"],
                "effectiveGame": round(self.applied[0] * self.config["game"] / 100) if self.applied else None,
                "effectiveChat": round(self.applied[1] * self.config["chat"] / 100) if self.applied else None,
                "followWheel": self.config["followWheel"], "alerts": self.config["alerts"],
                "ready": all(n in self.audio for n in CHANNELS),
                "defaultGame": getattr(self, "default_sink", "") == GAME,
                "apps": self.apps, "ruleCount": len(self.config["rules"]),
                "error": self.action_error or self.error, "controlError": self.control_error,
                "sidetone": choices.get("sidetone"), "sleep": choices.get("sleep"),
                "outputs": self.outputs, "fallback": self.config['fallback'],
                "fallbackActive": fallback_available and self.routed_target == self.config['fallback'],
                "fallbackAvailable": fallback_available,
                "fallbackVolume": sink_volume(self.audio.get(self.config['fallback'], {})) if fallback_available else None}

    def playback_after_cleanup(self, channel, fallback_active, names, default):
        """Where a Game/Chat stream goes when the virtual outputs close.

        Playback stays where the plugin was sending it: the active alternate
        output, or else the headset. The previously remembered default is only
        a default; moving a live call there could play it on open speakers.
        """
        if fallback_active:
            return self.output_target
        if self.physical in names:
            headset = self.headset_outputs[channel] if self.native_mix else self.physical
            return headset if headset in names else self.physical
        return default if default in names and default not in CHANNELS else ""

    def cleanup(self):
        try:
            default = run("pactl", "get-default-sink")
            sinks = run("pactl", "-f", "json", "list", "sinks", json_output=True)
            names = {s["name"] for s in sinks}
            fallback_active = (bool(self.config['fallback']) and self.output_target == self.config['fallback']
                               and self.output_target != self.physical)
            restore = self.output_target if fallback_active else (self.original_default if self.original_default in names else self.physical)
            if default in CHANNELS and restore in names:
                run("pactl", "set-default-sink", restore)
                default = restore
            indices = {s["index"]: s["name"] for s in sinks if s["name"] in CHANNELS}
            for stream in run("pactl", "-f", "json", "list", "sink-inputs", json_output=True):
                channel = indices.get(stream.get("sink"))
                target = self.playback_after_cleanup(channel, fallback_active, names, default) if channel else ""
                if target:
                    run("pactl", "move-sink-input", str(stream["index"]), target)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
        self.stop_loopbacks()

    def serve(self):
        # Share the lock with older installs that own the same PipeWire sinks.
        lock = (RUNTIME / "omarchy-arctis.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        SOCKET.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(SOCKET))
        SOCKET.chmod(0o600)
        server.listen(4)
        selector = selectors.DefaultSelector()
        selector.register(server, selectors.EVENT_READ)
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        next_poll, last_emit, last_json = 0, 0, ""
        try:
            while not self.stopping:
                now = time.monotonic()
                if now >= next_poll:
                    try:
                        previous = (self.product, self.receiver, self.power["online"])
                        self.headset()
                        if now - self.last_graph >= 2 or previous != (self.product, self.receiver, self.power["online"]):
                            self.graph()
                            self.last_graph = now
                        self.apply_volumes()
                        self.error = ""
                    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                        self.error = str(exc)[:300]
                    next_poll = now + (0.25 if self.receiver and WHEEL in self.capabilities
                                       and self.power["online"] is not False else 2)
                encoded = json.dumps(self.status(), separators=(",", ":"))
                if encoded != last_json or now - last_emit >= 2:
                    print(encoded, flush=True)
                    last_json, last_emit = encoded, now
                for key, _ in selector.select(timeout=max(0, min(0.25, next_poll - time.monotonic()))):
                    connection, _ = key.fileobj.accept()
                    with connection:
                        connection.settimeout(1)
                        try:
                            data = b""
                            while b"\n" not in data and len(data) <= 4096:
                                chunk = connection.recv(4096)
                                if not chunk:
                                    break
                                data += chunk
                            if len(data) > 4096:
                                raise ValueError("Request too large")
                            request = json.loads(data)
                            if not isinstance(request, dict):
                                raise ValueError("Expected a JSON object")
                            self.command(request)
                            self.action_error = ""
                            response = {"ok": True, "status": self.status()}
                        except (OSError, RuntimeError, ValueError, AttributeError, subprocess.TimeoutExpired) as exc:
                            self.action_error = str(exc)[:300]
                            response = {"ok": False, "error": self.action_error}
                        try:
                            connection.sendall((json.dumps(response) + "\n").encode())
                        except OSError:
                            pass
        finally:
            self.cleanup()
            selector.close()
            server.close()
            SOCKET.unlink(missing_ok=True)
            lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", help="Send a JSON command to the running plugin")
    parser.add_argument("--managed", action="store_true", help="Run under a disposable shell proxy")
    args = parser.parse_args()
    if args.command is not None:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(SOCKET))
            client.sendall((args.command + "\n").encode())
            response = b""
            while b"\n" not in response:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
            print(response.decode().strip())
            return 0 if json.loads(response).get("ok") else 1
    else:
        if args.managed:
            # Quickshell kills its direct child during component destruction.
            # A proxy allows the backend to receive TERM instead of KILL and
            # restore audio routes before removing its loopbacks.
            proxy_pid = os.getpid()
            child = os.fork()
            if child:
                _, status = os.waitpid(child, 0)
                return os.waitstatus_to_exitcode(status)
        backend = Backend()
        signal.signal(signal.SIGTERM, lambda *_: setattr(backend, "stopping", True))
        if args.managed:
            terminate_with_parent(proxy_pid)
        try:
            backend.serve()
        except BrokenPipeError:
            # The shell has closed stdout; serve's finally restores audio.
            sys.stdout = open(os.devnull, "w")
    return 0


if __name__ == "__main__":
    sys.exit(main())
