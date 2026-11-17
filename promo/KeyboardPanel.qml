// Capture-only replacement for the shell's layer window. The plugin's real
// content and controls render here without mapping a window on the desktop.
import QtQuick
import qs.Commons as Commons

Item {
  id: root
  property bool promoPanel: true
  property var anchorItem: null
  property var owner: null
  property var bar: null
  property var focusTarget: null
  property bool open: false
  property int contentWidth: 660
  property int contentHeight: 300
  default property alias contentItem: contentHolder.data
  width: contentWidth
  // A wider filming viewport fits the complete settings panel without scrolling.
  height: 550
  function fittedContentWidth(value) { return 660 }
  function fittedContentHeight(value, cap) { return Math.min(value + 28, cap) }
  property Item backdrop: Rectangle {
    parent: root
    anchors.fill: parent
    color: Commons.Color.popups.background
    border.color: Commons.Color.popups.border
    border.width: 1
  }
  property Item holder: Item {
    id: contentHolder
    parent: root
    x: 14
    y: 14
    width: root.width - 28
    height: root.height - 28
  }
}
