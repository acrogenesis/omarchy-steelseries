from collections import Counter
import copy
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arctis


class MixTests(unittest.TestCase):
    def setUp(self):
        inventory = patch.object(arctis, 'usb_products', return_value=Counter({'22a5': 1}))
        inventory.start()
        self.addCleanup(inventory.stop)

    def backend(self):
        backend = arctis.Backend()
        backend.products = Counter({'22a5': 1})
        backend.product, backend.receiver = '22a5', True
        backend.capabilities = {arctis.BATTERY, arctis.WHEEL, *arctis.SETTINGS.values()}
        backend.devices = {'22a5': {'capabilities': list(backend.capabilities)}}
        backend.discover_at = time.monotonic() + 60
        return backend

    def test_missing_receiver_is_disconnected_and_uses_alternate_output(self):
        backend = self.backend()
        backend.power = {'online': True, 'battery': 68, 'charging': False}
        backend.level = 64
        backend.control_error = 'Old query failure'
        with patch.object(arctis, 'usb_products', return_value=Counter()), patch.object(arctis, 'run') as command:
            backend.headset()
            command.assert_not_called()
        status = backend.status()
        self.assertIs(status['receiver'], False)
        self.assertIs(status['online'], False)
        self.assertIsNone(status['battery'])
        self.assertIsNone(status['wheel'])
        self.assertEqual(status['controlError'], '')
        self.assertEqual(arctis.output_target(status['online'], '', 'speaker', {'speaker'}, ''), 'speaker')

    def test_receiver_presence_is_independent_of_audio_profile_and_recovers(self):
        backend = self.backend()
        backend.physical = ''
        response = {'devices': [{'id_vendor': '0x1038', 'id_product': '0x22a5', 'battery': {'status': 'BATTERY_AVAILABLE', 'level': 67}, 'chatmix': 64, 'capabilities': [arctis.BATTERY, arctis.WHEEL]}]}
        with patch.object(arctis, 'usb_products', side_effect=[Counter(), Counter({'22a5': 1})]), patch.object(arctis, 'run', return_value=response):
            backend.headset()
            self.assertFalse(backend.status()['receiver'])
            backend.headset()
            self.assertTrue(backend.status()['receiver'])
            self.assertTrue(backend.status()['online'])
            self.assertEqual(backend.status()['battery'], 67)

    def test_hardware_endpoints_and_center(self):
        self.assertEqual(arctis.gains(0), (1, 0))
        self.assertEqual(arctis.gains(64), (1, 1))
        self.assertEqual(arctis.gains(128), (0, 1))
        self.assertEqual(arctis.gains(32), (1, .5))
        self.assertEqual(arctis.gains(96), (.5, 1))
        for value in (-1, 129, float('nan'), None, '64'):
            with self.assertRaises(ValueError):
                arctis.gains(value)

    def test_saved_route_overrides_communications_default(self):
        props = {'application.process.binary': 'vesktop', 'media.role': 'Communication'}
        self.assertEqual(arctis.route_for(props, {}), arctis.CHAT)
        self.assertEqual(arctis.route_for(props, {'vesktop': arctis.GAME}), arctis.GAME)

    def test_failed_hid_read_preserves_kernel_status_and_clears_wheel(self):
        with patch.object(arctis, 'read_power', return_value={'online': True, 'battery': 69, 'charging': False}):
            backend = self.backend()
            backend.level = 64
            with patch.object(arctis, 'run', side_effect=RuntimeError('Could not open device')):
                backend.headset()
            self.assertEqual(backend.power['battery'], 69)
            self.assertIsNone(backend.level)
            self.assertIn('Could not open', backend.control_error)

    def test_present_receiver_with_unknown_power_does_not_validate_cached_battery(self):
        with tempfile.TemporaryDirectory() as directory:
            battery = Path(directory)
            (battery / 'present').write_text('1\n')
            (battery / 'capacity').write_text('68\n')
            (battery / 'status').write_text('Unknown\n')
            with patch.object(Path, 'glob', return_value=iter([battery])):
                self.assertEqual(arctis.read_power('22a5'), {'online': None, 'battery': None, 'charging': False})
            (battery / 'status').write_text('Discharging\n')
            with patch.object(Path, 'glob', return_value=iter([battery])):
                self.assertEqual(arctis.read_power('22a5'), {'online': True, 'battery': 68, 'charging': False})

    def test_headset_power_off_clears_cached_battery_and_wheel_then_recovers(self):
        def device(status, level, errors=None):
            return {'devices': [{'id_vendor': '0x1038', 'id_product': '0x22a5', 'battery': {'status': status, 'level': level},
                                 'chatmix': 64, 'errors': errors or {}}]}
        snapshots = [device('BATTERY_AVAILABLE', 68),
                     device('BATTERY_UNAVAILABLE', -1, {'battery': 'Device is offline or not responding'}),
                     device('BATTERY_CHARGING', 67)]
        # Deliberately simulate a stale, still-positive kernel reading; the
        # receiver's explicit offline response must always take precedence.
        with patch.object(arctis, 'read_power', return_value={'online': True, 'battery': 68, 'charging': False}), \
             patch.object(arctis, 'run', side_effect=snapshots):
            backend = self.backend()
            backend.headset()
            self.assertEqual(backend.power, {'online': True, 'battery': 68, 'charging': False})
            backend.headset()
            self.assertEqual(backend.power, {'online': False, 'battery': None, 'charging': False})
            self.assertIsNone(backend.level)
            self.assertEqual(backend.control_error, '')
            backend.headset()
            self.assertEqual(backend.power, {'online': True, 'battery': 67, 'charging': True})
            self.assertEqual(backend.level, 64)

    def test_battery_permission_error_is_unknown_instead_of_offline_or_cached(self):
        response = {'devices': [{'id_vendor': '0x1038', 'id_product': '0x22a5', 'battery': {'status': 'BATTERY_UNAVAILABLE', 'level': -1},
                                 'chatmix': 64, 'errors': {'battery': 'Could not open device'}}]}
        with patch.object(arctis, 'read_power', return_value={'online': None, 'battery': None, 'charging': False}), \
             patch.object(arctis, 'run', return_value=response):
            backend = self.backend()
            backend.headset()
            self.assertIsNone(backend.power['online'])
            self.assertIsNone(backend.power['battery'])
            self.assertIsNone(backend.level)

    def test_wheel_attenuates_playback_without_resetting_user_volume(self):
        backend = self.backend()
        backend.config['game'], backend.config['chat'] = 40, 75
        backend.playbacks = {arctis.GAME: 20, arctis.CHAT: 21}
        backend.level = 96
        with patch.object(arctis, 'run') as run:
            backend.apply_volumes()
            self.assertEqual(run.call_args_list[0].args, ('pactl', 'set-sink-input-volume', '20', '50%'))
            self.assertEqual(run.call_args_list[1].args, ('pactl', 'set-sink-input-volume', '21', '100%'))
        self.assertEqual(backend.status()['effectiveGame'], 20)
        self.assertEqual(backend.status()['effectiveChat'], 75)

    def test_explicit_chat_selection_survives_graph_refresh(self):
        physical = 'alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.pro-output-0'
        sinks = [{'index': 1, 'name': physical, 'properties': {'alsa.components': 'USB1038:22a5'}}, {'index': 2, 'name': arctis.GAME}, {'index': 3, 'name': arctis.CHAT}]
        streams = [{'index': 10, 'sink': 3, 'properties': {'application.name': '<img src="https://example.invalid/x">'}}]
        backend = self.backend()
        backend.physical = physical
        backend.config['useDefault'] = False
        backend.config['rules'] = {}
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if args == ('pactl', '-f', 'json', 'list', 'sinks'):
                return copy.deepcopy(sinks)
            if args == ('pactl', '-f', 'json', 'list', 'sources'):
                return []
            if args == ('pactl', '-f', 'json', 'list', 'sink-inputs'):
                return copy.deepcopy(streams)
            if args == ('pactl', 'get-default-sink'):
                return arctis.GAME
            return ''
        with patch.object(arctis, 'run', side_effect=run), patch.object(backend, 'start_loopbacks'):
            backend.graph()
            backend.graph()
        self.assertFalse(any(c[1] == 'move-sink-input' for c in calls))
        self.assertEqual(backend.apps[0]['channel'], arctis.CHAT)
        self.assertEqual(backend.apps[0]['name'], streams[0]['properties']['application.name'])

    def test_lost_wheel_read_does_not_raise_attenuated_volume(self):
        backend = self.backend()
        backend.playbacks = {arctis.GAME: 20, arctis.CHAT: 21}
        backend.level = None
        backend.applied = (100, 0)
        with patch.object(arctis, 'run') as run:
            backend.apply_volumes()
            run.assert_not_called()
        self.assertEqual(backend.applied, (100, 0))

    def test_bad_config_cannot_inject_commands_or_exceed_volume_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'game': 200, 'chat': -4, 'followWheel': 'yes',
                                          'rules': {'app': '$(touch /tmp/nope)'}}))
            with patch.object(arctis, 'CONFIG', config):
                result = arctis.load_config()
            self.assertEqual(result['game'], 100)
            self.assertEqual(result['chat'], 100)
            self.assertEqual(result['rules'], {})

    def test_shell_kill_still_allows_backend_cleanup(self):
        # Exercise real fork / parent-death signals without opening audio/HID.
        with tempfile.TemporaryDirectory() as directory:
            code = '''
import sys,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import arctis
root=Path(sys.argv[2])
arctis.RUNTIME=root
arctis.SOCKET=root/'test.sock'
arctis.Backend.headset=lambda self: (root/'backend.pid').write_text(str(os.getpid()))
arctis.Backend.graph=lambda self: None
arctis.Backend.apply_volumes=lambda self: None
arctis.Backend.cleanup=lambda self: (root/'cleaned').write_text('yes')
sys.argv=['arctis.py','--managed']
arctis.main()
'''
            root = Path(directory)
            process = subprocess.Popen([sys.executable, '-c', code, str(Path(arctis.__file__).parent), directory],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not (root / 'backend.pid').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / 'backend.pid').exists())
                process.kill()
                process.wait(timeout=3)
                while not (root / 'cleaned').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / 'cleaned').exists(), 'Backend must run cleanup after shell kills its proxy')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                if (root / 'backend.pid').exists():
                    try:
                        os.kill(int((root / 'backend.pid').read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                process.stderr.close()

    def test_headset_choices_are_independent_and_survive_backend_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(arctis, 'CONFIG', Path(directory) / 'config.json'):
            backend = self.backend()
            self.assertIsNone(backend.status()['sidetone'])
            self.assertIsNone(backend.status()['sleep'])
            with patch.object(arctis, 'run', return_value={'devices': [{'id_vendor': '0x1038', 'id_product': '0x22a5', 'status': 'success'}]}):
                backend.command({'action': 'sidetone', 'product': '22a5', 'value': 1})
                backend.command({'action': 'sleep', 'product': '22a5', 'value': 15})
            self.assertEqual((backend.status()['sidetone'], backend.status()['sleep']), (1, 15))
            restarted = self.backend()
            self.assertEqual((restarted.status()['sidetone'], restarted.status()['sleep']), (1, 15))
            with patch.object(arctis, 'run', return_value={'devices': [{'id_vendor': '0x1038', 'id_product': '0x22a5', 'status': 'success'}]}):
                restarted.command({'action': 'sidetone', 'product': '22a5', 'value': 2})
            self.assertEqual((arctis.load_config()['sidetone'], arctis.load_config()['sleep']), (2, 15))

    def test_failed_headset_command_preserves_saved_choices(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(arctis, 'CONFIG', Path(directory) / 'config.json'):
            backend = self.backend()
            backend.config.update(sidetone=1, sleep=15)
            backend.save()
            with patch.object(arctis, 'run', return_value={'devices': [{'id_vendor': '0x1038', 'id_product': '0x22a5', 'status': 'error', 'errors': {'sidetone': 'Device offline'}}]}):
                with self.assertRaises(RuntimeError):
                    backend.command({'action': 'sidetone', 'product': '22a5', 'value': 3})
            self.assertEqual((arctis.load_config()['sidetone'], arctis.load_config()['sleep']), (1, 15))
            self.assertEqual((backend.status()['sidetone'], backend.status()['sleep']), (1, 15))

    def test_invalid_saved_headset_choices_stay_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            with patch.object(arctis, 'CONFIG', config):
                for bad in ({'sidetone': 4, 'sleep': -1}, {'sidetone': True, 'sleep': '15'}):
                    config.write_text(json.dumps(bad))
                    loaded = arctis.load_config()
                    self.assertIsNone(loaded['sidetone'])
                    self.assertIsNone(loaded['sleep'])

    def test_dynamic_text_is_plain_and_shared_tooltip_uses_no_app_names(self):
        base = Path(__file__).resolve().parents[1]
        self.assertIn('textFormat: Text.PlainText', (base / 'PlainText.qml').read_text())
        panel = (base / 'ArctisPanel.qml').read_text()
        self.assertIn('text: appRow.modelData.name', panel)
        widget = (base / 'BarWidget.qml').read_text()
        self.assertIn('tooltipText: strings.t("tooltip").arg(root.powerText())', widget)

    def test_alternate_output_list_excludes_headset_and_virtual_devices(self):
        sinks = [{'name': 'headset'}, {'name': arctis.GAME}, {'name': arctis.CHAT},
                 {'name': 'effects', 'properties': {'node.virtual': 'true'}},
                 {'name': 'speaker', 'description': '<img src="https://example.invalid/x">'},
                 {'name': 'unplugged', 'active_port': 'jack',
                  'ports': [{'name': 'jack', 'availability': 'not available'}]}]
        outputs = arctis.other_outputs(sinks, {'headset'})
        self.assertEqual([o['name'] for o in outputs], ['speaker', 'unplugged'])
        self.assertTrue(outputs[0]['available'])
        self.assertFalse(outputs[1]['available'])

    def test_output_destination_tracks_power_and_preserves_unknown_state(self):
        self.assertEqual(arctis.output_target(False, 'headset', 'speaker', {'speaker'}, 'headset'), 'speaker')
        self.assertEqual(arctis.output_target(True, 'headset', 'speaker', {'speaker'}, 'speaker'), 'headset')
        self.assertEqual(arctis.output_target(None, 'headset', 'speaker', {'speaker'}, 'speaker'), 'speaker')
        self.assertEqual(arctis.output_target(None, 'headset', 'speaker', {'speaker'}, 'headset'), 'headset')
        self.assertEqual(arctis.output_target(False, 'headset', 'speaker', set(), 'speaker'), 'headset')
        self.assertEqual(arctis.output_target(False, 'headset', '', {'speaker'}, 'speaker'), 'headset')

    def test_alternate_output_bypasses_cached_wheel_and_restores_it_on_return(self):
        backend = self.backend()
        backend.config['fallback'] = 'speaker'
        backend.output_target = 'speaker'
        backend.playbacks = {arctis.GAME: 20, arctis.CHAT: 21}
        backend.level = None
        backend.applied = (100, 0)
        with patch.object(arctis, 'run') as run:
            backend.apply_volumes()
            self.assertEqual(run.call_args_list[1].args, ('pactl', 'set-sink-input-volume', '21', '100%'))
            self.assertEqual(backend.applied, (100, 100))
            backend.output_target = backend.physical = 'headset'
            backend.level = 0
            backend.apply_volumes()
            self.assertEqual(run.call_args_list[-1].args, ('pactl', 'set-sink-input-volume', '21', '0%'))

    def test_alternate_output_choice_is_saved_and_rejects_unavailable_targets(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(arctis, 'CONFIG', Path(directory) / 'config.json'):
            backend = self.backend()
            backend.outputs = [{'name': 'speaker', 'available': True}, {'name': 'unplugged', 'available': False}]
            with patch.object(backend, 'graph'):
                backend.command({'action': 'fallback', 'value': 'speaker'})
                self.assertEqual(arctis.load_config()['fallback'], 'speaker')
                for invalid in ('headset', arctis.GAME, arctis.CHAT, 'unplugged', None):
                    with self.assertRaises(ValueError):
                        backend.command({'action': 'fallback', 'value': invalid})
                self.assertEqual(arctis.load_config()['fallback'], 'speaker')
                backend.command({'action': 'fallback', 'value': ''})
                self.assertEqual(arctis.load_config()['fallback'], '')

    def test_routing_moves_only_loopback_playback_between_outputs(self):
        physical = 'alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.pro-output-0'
        sinks = [{'index': 1, 'name': physical, 'properties': {'alsa.components': 'USB1038:22a5'}}, {'index': 2, 'name': arctis.GAME},
                 {'index': 3, 'name': arctis.CHAT}, {'index': 4, 'name': 'speaker'}]
        streams = [{'index': 20, 'sink': 1, 'properties': {'application.id': f"{arctis.PROJECT}.game", 'node.name': arctis.GAME + '_playback'}},
                   {'index': 21, 'sink': 1, 'properties': {'application.id': f"{arctis.PROJECT}.chat", 'node.name': arctis.CHAT + '_playback'}},
                   {'index': 22, 'sink': 3, 'properties': {'application.id': 'voice'}},
                   {'index': 23, 'sink': 4, 'properties': {'application.id': 'unrelated'}}]
        backend = self.backend()
        backend.physical = physical
        backend.config.update(fallback='speaker', useDefault=False, rules={})
        backend.power['online'] = False
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if args == ('pactl', '-f', 'json', 'list', 'sinks'):
                return copy.deepcopy(sinks)
            if args == ('pactl', 'get-default-sink'):
                return arctis.GAME
            if args == ('pactl', '-f', 'json', 'list', 'sink-inputs'):
                return copy.deepcopy(streams)
            return ''
        with patch.object(arctis, 'run', side_effect=run), patch.object(backend, 'start_loopbacks'):
            backend.graph()
            self.assertEqual([c for c in calls if c[1] == 'move-sink-input'],
                             [('pactl', 'move-sink-input', '20', 'speaker'), ('pactl', 'move-sink-input', '21', 'speaker')])
            self.assertEqual(backend.apps[0]['channel'], arctis.CHAT)
            self.assertEqual(len(backend.apps), 1)
            self.assertTrue(backend.status()['fallbackActive'])
            calls.clear()
            for stream in streams[:2]:
                stream['sink'] = None
            backend.graph()
            self.assertFalse(any(c[1] == 'move-sink-input' for c in calls))
            self.assertFalse(backend.status()['fallbackActive'])
            calls.clear()
            backend.power['online'] = True
            for stream in streams[:2]:
                stream['sink'] = 4
            backend.graph()
            self.assertEqual([c for c in calls if c[1] == 'move-sink-input'],
                             [('pactl', 'move-sink-input', '20', physical), ('pactl', 'move-sink-input', '21', physical)])
            self.assertFalse(backend.status()['fallbackActive'])

    def test_disabling_on_alternate_output_restores_playback_there(self):
        backend = self.backend()
        backend.config['fallback'] = 'speaker'
        backend.output_target = 'speaker'
        backend.original_default = 'headset'
        def run(*args, **kwargs):
            if args == ('pactl', 'get-default-sink'):
                return arctis.GAME
            if args == ('pactl', '-f', 'json', 'list', 'sinks'):
                return [{'index': 1, 'name': arctis.GAME}, {'index': 2, 'name': 'speaker'}, {'index': 3, 'name': 'headset'}]
            if args == ('pactl', '-f', 'json', 'list', 'sink-inputs'):
                return [{'index': 20, 'sink': 1}]
            return ''
        with patch.object(arctis, 'run', side_effect=run) as command:
            backend.cleanup()
            self.assertIn(unittest.mock.call('pactl', 'set-default-sink', 'speaker'), command.call_args_list)
            self.assertIn(unittest.mock.call('pactl', 'move-sink-input', '20', 'speaker'), command.call_args_list)

    def cleanup_calls(self, backend, default, sinks, streams):
        def run(*args, **kwargs):
            return {('pactl', 'get-default-sink'): default,
                    ('pactl', '-f', 'json', 'list', 'sinks'): sinks,
                    ('pactl', '-f', 'json', 'list', 'sink-inputs'): streams}.get(args, '')
        with patch.object(arctis, 'run', side_effect=run) as command, patch.object(backend, 'stop_loopbacks'):
            backend.cleanup()
        return [c.args for c in command.call_args_list if c.args[1] in ('move-sink-input', 'set-default-sink')]

    def test_disabling_keeps_a_chat_call_on_the_headset_not_old_speakers(self):
        # Review finding: speakers were the default before "Use Game as default", the user
        # later picked a private default, and a call plays through Chat while connected.
        headset = 'alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.pro-output-0'
        backend = self.backend()
        backend.physical = headset
        backend.headset_outputs = {arctis.GAME: headset, arctis.CHAT: headset}
        backend.output_target = headset
        backend.original_default = 'speaker'
        backend.config['fallback'] = ''
        sinks = [{'index': 1, 'name': headset}, {'index': 2, 'name': arctis.GAME},
                 {'index': 3, 'name': arctis.CHAT}, {'index': 4, 'name': 'speaker'}, {'index': 5, 'name': 'private'}]
        calls = self.cleanup_calls(backend, 'private', sinks, [{'index': 30, 'sink': 3}])
        self.assertEqual(calls, [('pactl', 'move-sink-input', '30', headset)])
        # When Game is still the default, the old default comes back, but playback still stays private.
        calls = self.cleanup_calls(backend, arctis.GAME, sinks, [{'index': 30, 'sink': 3}, {'index': 31, 'sink': 2}])
        self.assertEqual(calls, [('pactl', 'set-default-sink', 'speaker'),
                                 ('pactl', 'move-sink-input', '30', headset), ('pactl', 'move-sink-input', '31', headset)])

    def test_disabling_split_headset_returns_each_bus_to_its_endpoint(self):
        game, chat = 'alsa_output.usb-arctis.pro-output-1', 'alsa_output.usb-arctis.pro-output-0'
        backend = self.backend()
        backend.physical, backend.native_mix, backend.output_target = game, True, game
        backend.headset_outputs = {arctis.GAME: game, arctis.CHAT: chat}
        backend.original_default = 'speaker'
        sinks = [{'index': 1, 'name': chat}, {'index': 2, 'name': game}, {'index': 3, 'name': arctis.GAME},
                 {'index': 4, 'name': arctis.CHAT}, {'index': 5, 'name': 'speaker'}]
        calls = self.cleanup_calls(backend, arctis.GAME, sinks, [{'index': 20, 'sink': 3}, {'index': 21, 'sink': 4}])
        self.assertEqual(calls, [('pactl', 'set-default-sink', 'speaker'),
                                 ('pactl', 'move-sink-input', '20', game), ('pactl', 'move-sink-input', '21', chat)])

    def test_cleanup_without_headset_follows_the_restored_default(self):
        backend = self.backend()
        backend.physical, backend.output_target = '', ''
        backend.original_default = 'speaker'
        sinks = [{'index': 2, 'name': arctis.GAME}, {'index': 3, 'name': arctis.CHAT}, {'index': 4, 'name': 'speaker'}]
        calls = self.cleanup_calls(backend, arctis.CHAT, sinks, [{'index': 30, 'sink': 3}])
        self.assertEqual(calls, [('pactl', 'set-default-sink', 'speaker'), ('pactl', 'move-sink-input', '30', 'speaker')])
        backend.original_default = ''
        calls = self.cleanup_calls(backend, arctis.CHAT, sinks, [{'index': 30, 'sink': 3}])
        self.assertEqual(calls, [], 'With nowhere known to go, PipeWire picks the destination')

    def test_restart_resumes_game_default_after_alternate_output_cleanup(self):
        physical = 'alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.pro-output-0'
        backend = self.backend()
        backend.physical = physical
        backend.config.update(useDefault=True, fallback='speaker')
        backend.power['online'] = True
        def run(*args, **kwargs):
            if args == ('pactl', '-f', 'json', 'list', 'sinks'):
                return [{'index': 1, 'name': physical, 'properties': {'alsa.components': 'USB1038:22a5'}}, {'index': 2, 'name': arctis.GAME},
                        {'index': 3, 'name': arctis.CHAT}, {'index': 4, 'name': 'speaker'}]
            if args == ('pactl', 'get-default-sink'):
                return 'speaker'
            if args == ('pactl', '-f', 'json', 'list', 'sink-inputs'):
                return []
            return ''
        with patch.object(arctis, 'run', side_effect=run) as command, patch.object(backend, 'start_loopbacks'):
            backend.graph()
            self.assertIn(unittest.mock.call('pactl', 'set-default-sink', arctis.GAME), command.call_args_list)
            self.assertTrue(backend.status()['defaultGame'])

    def test_alternate_device_volume_reads_live_sink_without_overwriting_trims(self):
        backend = self.backend()
        backend.config.update(fallback='speaker', game=60, chat=80)
        backend.outputs = [{'name': 'speaker', 'available': True}]
        backend.audio = {'speaker': {'volume': {'left': {'value': 32768}, 'right': {'value': 32768}}}}
        self.assertEqual(backend.status()['fallbackVolume'], 50)
        backend.audio['speaker']['volume']['left']['value'] = 0
        backend.audio['speaker']['volume']['right']['value'] = 0
        self.assertEqual(backend.status()['fallbackVolume'], 0)
        self.assertEqual((backend.status()['game'], backend.status()['chat']), (60, 80))
        backend.outputs[0]['available'] = False
        self.assertIsNone(backend.status()['fallbackVolume'])

    def test_alternate_volume_only_changes_selected_active_device(self):
        backend = self.backend()
        backend.config['fallback'] = 'speaker'
        backend.routed_target = 'speaker'
        backend.outputs = [{'name': 'speaker', 'available': True}]
        backend.power['online'] = False
        with patch.object(arctis, 'run') as command, patch.object(backend, 'graph') as refresh:
            backend.command({'action': 'fallbackVolume', 'output': 'speaker', 'value': 45})
            command.assert_called_once_with('pactl', 'set-sink-volume', 'speaker', '45%')
            refresh.assert_called_once()

    def test_alternate_volume_rejects_stale_target_inactive_device_and_invalid_values(self):
        backend = self.backend()
        backend.config['fallback'] = 'speaker'
        backend.routed_target = 'speaker'
        backend.outputs = [{'name': 'speaker', 'available': True}]
        backend.power['online'] = False
        with patch.object(arctis, 'run') as command:
            for value in (-1, 101, True, '50'):
                with self.assertRaises(ValueError):
                    backend.command({'action': 'fallbackVolume', 'output': 'speaker', 'value': value})
            with self.assertRaises(ValueError):
                backend.command({'action': 'fallbackVolume', 'output': 'headset', 'value': 50})
            backend.power['online'] = True
            with self.assertRaises(ValueError):
                backend.command({'action': 'fallbackVolume', 'output': 'speaker', 'value': 50})
            backend.power['online'] = False
            backend.outputs[0]['available'] = False
            with self.assertRaises(ValueError):
                backend.command({'action': 'fallbackVolume', 'output': 'speaker', 'value': 50})
            command.assert_not_called()


class ReceiverPresenceTests(unittest.TestCase):
    def test_usb_identity_detection_and_unavailable_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(arctis.usb_products(root), Counter())
            interface = root / '1-1:1.3'
            interface.mkdir()
            device = root / '1-1'
            device.mkdir()
            (device / 'idVendor').write_text('1038\n')
            (device / 'idProduct').write_text('22a5\n')
            self.assertEqual(arctis.usb_products(root), Counter({'22a5': 1}))
            (device / 'idProduct').write_text('9999\n')
            self.assertEqual(arctis.usb_products(root), Counter({'9999': 1}))
            (device / 'idVendor').write_text('1234\n')
            self.assertEqual(arctis.usb_products(root), Counter())
            with self.assertRaises(FileNotFoundError):
                arctis.usb_products(root / 'missing')


class SteelSeriesDiscoveryTests(unittest.TestCase):
    nova_7p = {'id_vendor': '0x1038', 'id_product': '0x22a7', 'product': 'Arctis Nova 7P',
               'capabilities': [arctis.BATTERY, arctis.SETTINGS['sleep']]}
    other_vendor = {**nova_7p, 'id_vendor': '0x9999'}

    def test_nova_7p_uses_battery_and_auto_off_without_requesting_chatmix(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(arctis, 'CONFIG', Path(directory) / 'config.json'):
            backend = arctis.Backend()
            backend.config.update(sidetone=1, sleep=30)
            battery = {**self.nova_7p, 'battery': {'status': 'BATTERY_AVAILABLE', 'level': 71}}
            status_response = {'devices': [battery]}
            setting_response = {'devices': [{**self.nova_7p, 'status': 'success'}]}
            with patch.object(arctis, 'usb_products', return_value=Counter({'22a7': 1})), \
                 patch.object(arctis, 'run', side_effect=[{'devices': [self.nova_7p]}, status_response, setting_response]) as command:
                backend.headset()
                status = backend.status()
                self.assertEqual(status['headsetName'], 'Arctis Nova 7P')
                self.assertEqual(status['battery'], 71)
                self.assertTrue(status['supportsSleep'])
                self.assertFalse(status['supportsWheel'])
                self.assertFalse(status['supportsSidetone'])
                self.assertIsNone(status['sleep'])
                self.assertEqual(command.call_args.args[1:], ('-d', '1038:22a7', '-b', '-o', 'json'))
                for action, product in (('sidetone', '22a7'), ('sleep', '22a5')):
                    with self.assertRaises(ValueError):
                        backend.command({'action': action, 'product': product, 'value': 1})
                self.assertEqual(command.call_count, 2)
                backend.command({'action': 'sleep', 'product': '22a7', 'value': 15})
                self.assertEqual(command.call_args.args[1:], ('-d', '1038:22a7', '--inactive-time', '15', '-o', 'json'))
            self.assertEqual(arctis.load_config()['sleep'], 30)
            self.assertEqual(arctis.load_config()['headsetSettings'], {'22a7': {'sleep': 15}})

    def test_discovery_keeps_selected_model_until_unplugged(self):
        nova_7x = {**self.nova_7p, 'id_product': '0x22a5'}
        backend = arctis.Backend()
        inventories = [Counter({'22a7': 1}), Counter({'22a7': 1, '22a5': 1}), Counter({'22a5': 1})]
        responses = [{'devices': [self.nova_7p]}, {'devices': [nova_7x, self.nova_7p]}, {'devices': [nova_7x]}]
        with patch.object(arctis, 'usb_products', side_effect=inventories), patch.object(arctis, 'run', side_effect=responses):
            for expected in ('22a7', '22a7', '22a5'):
                backend.discover()
                self.assertEqual(backend.product, expected)
                self.assertTrue(backend.receiver)

    def test_duplicate_receiver_and_other_vendor_cannot_be_selected(self):
        backend = arctis.Backend()
        with patch.object(arctis, 'usb_products', return_value=Counter({'22a7': 1})), \
             patch.object(arctis, 'run', return_value={'devices': [self.other_vendor]}):
            backend.headset()
            self.assertFalse(backend.receiver)
        with patch.object(arctis, 'usb_products', return_value=Counter({'22a7': 2})), \
             patch.object(arctis, 'run', return_value={'devices': [self.nova_7p]}) as command:
            backend.headset()
            self.assertIsNone(backend.receiver)
            self.assertIn('one headset', backend.control_error)
            command.assert_called_once()  # Enumeration only, no hardware status query.

    def test_split_headset_routes_each_bus_to_its_hardware_endpoint(self):
        backend = arctis.Backend()
        backend.product, backend.receiver = '1290', True
        backend.products = Counter({'1290': 1, '1294': 1})
        backend.capabilities = {arctis.WHEEL}
        backend.config.update(fallback='speaker', useDefault=False, followWheel=True)
        backend.level = 128
        backend.power['online'] = True
        game, chat = 'alsa_output.usb-arctis.pro-output-1', 'alsa_output.usb-arctis.pro-output-0'
        sinks = [
            {'index': 1, 'name': chat, 'properties': {'device.vendor.id': '0x1038', 'device.product.id': '0x1294', 'alsa.device': '0'}},
            {'index': 2, 'name': game, 'properties': {'device.vendor.id': '0x1038', 'device.product.id': '0x1294', 'alsa.device': '1'}},
            {'index': 3, 'name': arctis.GAME}, {'index': 4, 'name': arctis.CHAT}, {'index': 5, 'name': 'speaker'},
        ]
        streams = [{'index': index, 'sink': 5, 'properties': {'application.id': f'{arctis.PROJECT}.{role}',
                    'node.name': f'arctis_{role}_playback'}} for index, role in ((20, 'game'), (21, 'chat'))]

        def run(*args, **kwargs):
            return {('pactl', '-f', 'json', 'list', 'sinks'): sinks,
                    ('pactl', '-f', 'json', 'list', 'sink-inputs'): streams,
                    ('pactl', 'get-default-sink'): arctis.GAME}.get(args, '')

        with patch.object(arctis, 'run', side_effect=run) as command, patch.object(backend, 'start_loopbacks'):
            backend.graph()
            backend.apply_volumes()
            self.assertEqual(backend.physical, game)
            self.assertEqual([o['name'] for o in backend.outputs], ['speaker'])
            self.assertFalse(backend.status()['supportsWheel'])
            self.assertIn(unittest.mock.call('pactl', 'move-sink-input', '20', game), command.call_args_list)
            self.assertIn(unittest.mock.call('pactl', 'move-sink-input', '21', chat), command.call_args_list)
            self.assertEqual(backend.applied, (100, 100))
            self.assertFalse(backend.status()['fallbackActive'])
            backend.power['online'] = False
            streams[0]['sink'], streams[1]['sink'] = 2, 1
            backend.graph()
            self.assertIn(unittest.mock.call('pactl', 'move-sink-input', '20', 'speaker'), command.call_args_list)
            self.assertIn(unittest.mock.call('pactl', 'move-sink-input', '21', 'speaker'), command.call_args_list)
            self.assertTrue(backend.status()['fallbackActive'])


if __name__ == '__main__':
    unittest.main()
