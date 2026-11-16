import QtQuick
import QtTest
import "../../"

TestCase {
  id: testCase
  name: "LiveVolume"
  property var events: []
  VolumeStream {
    id: stream
    onDispatchRequested: function(request) { testCase.events = testCase.events.concat([request]) }
  }
  function init() { stream.cadence.stop(); stream.pending = []; events = [] }
  function cleanup() { stream.cadence.stop(); stream.pending = [] }
  function test_changes_apply_while_held_without_release() {
    stream.push({action: "volume", channel: "game", value: 80}, false)
    compare(events.length, 1)
    for (var i = 79; i >= 0; i--) stream.push({action: "volume", channel: "game", value: i}, false)
    compare(events.length, 1)
    wait(80)
    compare(events.length, 2)
    compare(events[1].value, 0)
  }
  function test_release_flushes_final_value_without_replaying_stale_levels() {
    stream.push({action: "fallbackVolume", output: "speaker", value: 80}, false)
    stream.push({action: "fallbackVolume", output: "speaker", value: 60}, false)
    stream.push({action: "fallbackVolume", output: "speaker", value: 35}, true)
    compare(events.length, 2)
    compare(events[1].value, 35)
    wait(80)
    compare(events.length, 2)
  }
  function test_channels_are_independent_and_flush_preserves_pending_movement() {
    stream.push({action: "volume", channel: "game", value: 80}, false)
    stream.push({action: "volume", channel: "game", value: 60}, false)
    stream.push({action: "volume", channel: "chat", value: 25}, false)
    stream.flush()
    compare(events.length, 3)
    compare(events[1].channel, "game")
    compare(events[1].value, 60)
    compare(events[2].channel, "chat")
    compare(events[2].value, 25)
  }
}
