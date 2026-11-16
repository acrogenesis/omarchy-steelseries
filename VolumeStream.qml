import QtQuick
import "CommandQueue.js" as Queue

QtObject {
  id: root
  property var pending: []
  signal dispatchRequested(var request)

  function flush() {
    var batch = pending
    pending = []
    for (var i = 0; i < batch.length; i++) dispatchRequested(batch[i])
  }
  function push(request, finalValue) {
    pending = Queue.enqueue(pending, request)
    if (finalValue) {
      cadence.stop()
      flush()
    } else if (!cadence.running) {
      flush() // Apply the first movement immediately.
      cadence.start()
    }
  }
  property Timer cadence: Timer {
    interval: 50
    repeat: true
    onTriggered: root.pending.length ? root.flush() : stop()
  }
}
