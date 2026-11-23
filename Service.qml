import QtQuick
import Quickshell.Io
import "CommandQueue.js" as Queue

Item {
  id: root
  readonly property string backend: decodeURIComponent(String(Qt.resolvedUrl("arctis.py")).replace(/^file:\/\//, ""))
  property var snapshot: ({})
  property double lastUpdate: 0
  property double now: Date.now()
  readonly property bool fresh: lastUpdate > 0 && now - lastUpdate < 7000
  readonly property var statusModel: fresh ? snapshot : ({error: "SteelSeries headset service unavailable"})
  property var pending: []
  property bool stopping: false

  function request(value) {
    volumes.flush()
    enqueue(value)
  }
  function requestVolume(value, finalValue) {
    volumes.push(value, finalValue === true)
  }
  function enqueue(value) {
    pending = Queue.enqueue(pending, value)
    dispatch()
  }
  function dispatch() {
    if (operation.running || pending.length === 0) return
    var next = pending[0]
    pending = pending.slice(1)
    operation.command = ["python3", root.backend, "--command", JSON.stringify(next)]
    operation.running = true
  }
  Process {
    id: daemon
    command: ["python3", root.backend, "--managed"]
    running: true
    stdout: SplitParser {
      onRead: function(line) {
        if (line.length > 65536) return
        try {
          const value = JSON.parse(line)
          if (value && typeof value === "object" && !Array.isArray(value)) {
            root.snapshot = value
            root.lastUpdate = Date.now()
            root.now = Date.now()
          }
        } catch (e) { console.warn("SteelSeries Headsets: invalid status") }
      }
    }
    onExited: if (!root.stopping) retry.restart()
  }
  Process {
    id: operation
    onExited: Qt.callLater(root.dispatch)
  }
  VolumeStream {
    id: volumes
    onDispatchRequested: function(request) { root.enqueue(request) }
  }
  Timer { id: retry; interval: 3000; onTriggered: daemon.running = true }
  Timer { interval: 1000; repeat: true; running: true; onTriggered: root.now = Date.now() }
  Component.onDestruction: { root.stopping = true; daemon.signal(15) }
}
