import QtQuick
import Quickshell.Io
import qs.Commons as Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "acrogenesis.arctis"
  readonly property var service: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
  readonly property var statusModel: service ? service.statusModel : ({})
  readonly property bool opened: panel.opened
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
  function open() { panel.open() }
  function close() { panel.close() }
  function toggle() { panel.toggle() }
  function powerText() {
    return statusModel.online === true ? strings.t("on") : statusModel.online === false ? strings.t("off") : strings.t("unknown")
  }
  IpcHandler {
    target: root.moduleName
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function status(): string { return JSON.stringify(root.statusModel) }
  }
  WidgetButton {
    id: button
    bar: root.bar
    anchors.fill: parent
    text: "󰋋 " + (root.statusModel.online === true && root.statusModel.battery !== null && root.statusModel.battery !== undefined
      ? Commons.I18n.formatNumber(root.statusModel.battery) + "%" : root.powerText())
    dimmed: root.statusModel.online !== true
    active: root.statusModel.online === true && root.statusModel.battery !== null && root.statusModel.battery <= 20
    tooltipText: strings.t("tooltip").arg(root.powerText())
    onPressed: root.toggle()
  }
  ArctisPanel {
    id: panel
    bar: root.bar
    anchorItem: button
    hostWidget: root
    service: root.service
  }
}
