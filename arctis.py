#!/usr/bin/env python3
"""Omarchy Arctis backend. Python stdlib + PipeWire tools + HeadsetControl."""
import argparse
import ctypes
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time

GAME, CHAT = "arctis_game", "arctis_chat"
CHANNELS = (GAME, CHAT)
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy/arctis/config.json"
SOCKET = RUNTIME / "omarchy-arctis.sock"


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


def receiver_present(directory=Path('/sys/bus/usb/devices')):
    try:
        devices = list(directory.iterdir())
    except OSError:
        return None
    uncertain = False
    for device in devices:
        try:
            if ((device / 'idVendor').read_text().strip().lower() == '1038'
                    and (device / 'idProduct').read_text().strip().lower() == '22a5'):
                return True
        except PermissionError:
            uncertain = True
        except OSError:
            pass  # USB interfaces have no idVendor/idProduct files.
    return None if uncertain else False


def read_power():
    for path in Path("/sys/class/power_supply").glob("steelseries*1038:22A5*"):
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


def other_outputs(sinks, physical):
    outputs = []
    for sink in sinks:
        name = sink['name']
        if name in (*CHANNELS, physical) or 'SteelSeries_Arctis_Nova_7X' in name:
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
                "useDefault": False, "sidetone": None, "sleep": None, "fallback": ""}
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
        self.power = read_power()
        self.receiver = None
        self.last_low = False
        self.last_graph = 0
        self.hsc = shutil.which("headsetcontrol") or str(Path.home() / ".local/lib/omarchy-arctis/headsetcontrol")
        self.stopping = False

    def save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, indent=2) + "\n")
        temporary.replace(CONFIG)

    def headset(self):
        self.receiver = receiver_present()
        if self.receiver is False:
            self.power = {'online': False, 'battery': None, 'charging': False}
            self.level = None
            self.control_error = ''
            self.last_low = False
            return
        self.power = read_power()
        try:
            data = run(self.hsc, "-d", "1038:22a5", "-b", "-m", "-o", "json", json_output=True)
            devices = data.get("devices", [])
            device = next((d for d in devices if d.get("id_product", "").lower() == "0x22a5"), None)
            if not device:
                self.level = None
                self.control_error = "Headset receiver not found"
                return
            errors = device.get("errors", {})
            battery = device.get("battery", {})
            status = battery.get("status")
            if status in ("BATTERY_AVAILABLE", "BATTERY_CHARGING"):
                level = battery.get("level")
                if type(level) is int and 0 <= level <= 100:
                    self.power = {"online": True, "battery": level, "charging": status == "BATTERY_CHARGING"}
            elif status == "BATTERY_UNAVAILABLE":
                error = str(errors.get("battery", "")).strip().casefold()
                # HeadsetControl serializes DeviceOffline using its generic
                # message, not the device implementation's "not connected" detail.
                if error.startswith("device is offline") or "not connected" in error:
                    self.power = {"online": False, "battery": None, "charging": False}
            level = device.get("chatmix")
            if self.power["online"] is not True:
                # The receiver also retains a valid-looking dial value offline.
                self.level = None
                self.control_error = ""
            elif type(level) is int and 0 <= level <= 128:
                self.level = level
                self.control_error = ""
            else:
                self.level = None
                self.control_error = str(errors.get("chatmix", "Wheel status unavailable"))[:300]
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            self.level = None
            self.control_error = str(exc)[:300]
        low = self.power["online"] is True and self.power["battery"] is not None and self.power["battery"] <= 20
        if low and not self.last_low and self.config["alerts"] and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "--app-name=Arctis", "--icon=audio-headphones",
                              "Arctis battery low", f"Battery: {self.power['battery']}%"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.last_low = low

    def start_loopbacks(self):
        for name, label in ((GAME, "Game"), (CHAT, "Chat")):
            process = self.loopbacks.get(name)
            if process is not None and process.poll() is None:
                continue
            capture = {"media.class": "Audio/Sink", "node.name": name,
                       "node.description": f"Arctis {label}", "node.virtual": True,
                       "priority.session": 0, "application.id": "omarchy-arctis"}
            playback = {"node.name": f"{name}_playback", "node.description": f"Arctis {label} playback",
                        "node.dont-fallback": True, "node.passive": True,
                        "stream.dont-remix": True, "application.id": f"omarchy-arctis.{label.lower()}"}
            backend_pid = os.getpid()
            self.loopbacks[name] = subprocess.Popen([
                "pw-loopback", "-n", f"omarchy_{name}", "-c", "2", "-m", "[ FL FR ]",
                "-P", self.output_target or self.physical, "-i", json.dumps(capture), "-o", json.dumps(playback)],
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
        physical = next((s["name"] for s in sinks if s["name"].startswith("alsa_output.")
                         and s.get("properties", {}).get("device.product.id", "").lower() in ("0x22a5", "22a5")), "")
        # Some PipeWire profiles don't copy USB properties onto the sink.
        physical = physical or next((s["name"] for s in sinks if s["name"].startswith("alsa_output.")
                                     and "SteelSeries_Arctis_Nova_7X" in s["name"]), "")
        if physical != self.physical:
            self.stop_loopbacks()
            self.physical = physical
            self.adopted = False
        self.audio = {s["name"]: s for s in sinks}
        self.outputs = other_outputs(sinks, physical)
        available = {o['name'] for o in self.outputs if o['available']}
        self.output_target = output_target(self.power['online'], physical, self.config['fallback'], available, self.output_target)
        if self.output_target:
            self.start_loopbacks()
        self.default_sink = run("pactl", "get-default-sink")
        if not self.adopted and all(n in self.audio for n in CHANNELS):
            self.adopted = True
            resumable = {physical, self.output_target}
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
            own = str(props.get("application.id", "")).startswith("omarchy-arctis")
            if own:
                for channel in CHANNELS:
                    if props.get("node.name") == f"{channel}_playback":
                        playbacks[channel] = stream["index"]
                        current_output = by_index.get(stream.get('sink'))
                        if self.output_target in self.audio and current_output and current_output != self.output_target:
                            run('pactl', 'move-sink-input', str(stream['index']), self.output_target)
                            current_output = self.output_target
                        if current_output and current_output == self.output_target:
                            routed_channels.add(channel)
                continue
            if props.get("application.process.binary") == "pw-loopback":
                continue
            current = by_index.get(stream.get("sink"), "")
            if current not in (*CHANNELS, physical) or not current:
                continue
            identity = (stream["index"], app_key(props))
            seen.add(identity)
            explicit = app_key(props) in self.config["rules"]
            desired = route_for(props, self.config["rules"])
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
        fallback_active = bool(self.config['fallback']) and self.output_target == self.config['fallback']
        if self.config["followWheel"] and self.level is None and self.applied is not None and not fallback_active:
            return  # A failed read must not suddenly lift the last attenuation.
        attenuation = gains(self.level) if self.config["followWheel"] and self.level is not None and not fallback_active else (1, 1)
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
            flag = "--sidetone" if action == "sidetone" else "--inactive-time"
            value = (0, 43, 85, 128)[value] if action == "sidetone" else value
            data = run(self.hsc, "-d", "1038:22a5", flag, str(value), "-o", "json", json_output=True)
            device = next(iter(data.get("devices", [])), {})
            if not device or device.get("errors") or device.get("status") != "success":
                raise RuntimeError(str(device.get("errors", "Setting could not be applied")))
            # Persist each setting only after the hardware command succeeds.
            # These are last-applied choices, not device readback.
            self.config[action] = request["value"]
            self.save()
        elif action != "status":
            raise ValueError("Unknown action")
        self.apply_volumes()

    def status(self):
        fallback_available = any(o['name'] == self.config['fallback'] and o['available'] for o in self.outputs)
        return {**self.power, "receiver": self.receiver, "wheel": self.level,
                "wheelAvailable": self.level is not None, "physical": self.physical,
                "game": self.config["game"], "chat": self.config["chat"],
                "effectiveGame": round(self.applied[0] * self.config["game"] / 100) if self.applied else None,
                "effectiveChat": round(self.applied[1] * self.config["chat"] / 100) if self.applied else None,
                "followWheel": self.config["followWheel"], "alerts": self.config["alerts"],
                "ready": all(n in self.audio for n in CHANNELS),
                "defaultGame": getattr(self, "default_sink", "") == GAME,
                "apps": self.apps, "ruleCount": len(self.config["rules"]),
                "error": self.action_error or self.error, "controlError": self.control_error,
                "sidetone": self.config["sidetone"], "sleep": self.config["sleep"],
                "outputs": self.outputs, "fallback": self.config['fallback'],
                "fallbackActive": bool(self.config['fallback']) and self.routed_target == self.config['fallback'],
                "fallbackAvailable": fallback_available,
                "fallbackVolume": sink_volume(self.audio.get(self.config['fallback'], {})) if fallback_available else None}

    def cleanup(self):
        try:
            default = run("pactl", "get-default-sink")
            sinks = run("pactl", "-f", "json", "list", "sinks", json_output=True)
            names = {s["name"] for s in sinks}
            fallback_active = bool(self.config['fallback']) and self.output_target == self.config['fallback']
            restore = self.output_target if fallback_active else (self.original_default if self.original_default in names else self.physical)
            if restore in names:
                if default in CHANNELS:
                    run("pactl", "set-default-sink", restore)
                indices = {s["index"] for s in sinks if s["name"] in CHANNELS}
                for stream in run("pactl", "-f", "json", "list", "sink-inputs", json_output=True):
                    if stream.get("sink") in indices:
                        run("pactl", "move-sink-input", str(stream["index"]), restore)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
        self.stop_loopbacks()

    def serve(self):
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
                        self.headset()
                        if now - self.last_graph >= 2:
                            self.graph()
                            self.last_graph = now
                        self.apply_volumes()
                        self.error = ""
                    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                        self.error = str(exc)[:300]
                    next_poll = now + (0.25 if self.power["online"] is True else 2)
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
