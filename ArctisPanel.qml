pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "PanelModel.js" as Model

Panel {
  id: root
  moduleName: "acrogenesis.arctis"
  manageIpc: false
  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  property bool appsExpanded: false
  property bool moreExpanded: false
  readonly property var statusModel: service ? service.statusModel : ({})
  readonly property bool hasSettings: !!(statusModel.supportsWheel || statusModel.supportsSidetone
    || statusModel.supportsSleep || statusModel.supportsBattery)
  readonly property bool canConfigure: statusModel.receiver === true && statusModel.online !== false
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property int appCount: (statusModel.apps || []).length
  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
  function send(value) { if (service) service.request(value) }
  function sendVolume(value, finalValue) { if (service) service.requestVolume(value, finalValue) }
  function number(value) { return value === null || value === undefined ? "—" : Commons.I18n.formatNumber(value) }
  function switchPanel(direction) {
    return bar && bar.switchPanelFrom ? bar.switchPanelFrom(hostWidget || root, direction) : false
  }
  onOpenedChanged: if (!opened) {
    appsExpanded = false
    moreExpanded = false
    if (outputPicker) outputPicker.close()
    if (scroll) scroll.contentY = 0
  }
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(bodyColumn.implicitHeight, Style.space(620))
    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      blocked: outputPicker.popupOpen
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        var key = text.toLowerCase()
        if (key === "a") root.appsExpanded = !root.appsExpanded
        else if (key === "s") root.moreExpanded = !root.moreExpanded
        else if (key === "o") outputPicker.open()
      }
      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: bodyColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
        Column {
          id: bodyColumn
          width: parent.width
          spacing: Style.space(10)
          Row {
            width: parent.width
            spacing: Style.space(8)
            PlainText {
              width: Math.max(0, parent.width - powerText.implicitWidth - parent.spacing)
              text: root.statusModel.headsetName || strings.t("headset")
              color: root.foreground
              font.pixelSize: Style.font.body * 1.4
              wrapMode: Text.NoWrap
              elide: Text.ElideRight
            }
            PlainText {
              id: powerText
              anchors.verticalCenter: parent.verticalCenter
              color: root.foreground
              opacity: 0.75
              text: root.statusModel.receiver === false ? strings.t("receiverDisconnected") : root.statusModel.online === true
                ? root.statusModel.battery === null ? strings.t("on")
                  : strings.t(root.statusModel.charging ? "shortCharging" : "shortBattery").arg(root.number(root.statusModel.battery))
                : root.statusModel.online === false ? strings.t("off")
                  : root.statusModel.receiver === true && root.statusModel.supportsBattery === false
                    ? strings.t("usbConnected") : strings.t("unknown")
            }
          }
          PlainText {
            width: parent.width
            visible: !!root.statusModel.error || !!root.statusModel.controlError
            color: root.bar ? root.bar.urgent : Color.urgent
            text: root.statusModel.error || root.statusModel.controlError || ""
          }
          Button {
            width: parent.width
            visible: !root.statusModel.defaultGame
            text: strings.t("setDefault")
            enabled: !!root.statusModel.ready
            foreground: root.foreground
            focusable: true
            onClicked: root.send({action: "default"})
          }
          Repeater {
            model: ["game", "chat"]
            Row {
              id: volumeRow
              required property string modelData
              width: bodyColumn.width
              spacing: Style.space(8)
              PlainText {
                width: Style.space(48)
                anchors.verticalCenter: parent.verticalCenter
                color: root.foreground
                text: strings.t(volumeRow.modelData)
                wrapMode: Text.NoWrap
                elide: Text.ElideRight
              }
              PanelSlider {
                id: gameVolume
                width: parent.width - Style.space(48 + 45 + 16)
                bar: root.bar
                minimum: 0
                maximum: 100
                integer: true
                step: 5
                enabled: !!root.statusModel.ready
                value: root.statusModel[volumeRow.modelData] === undefined ? 100 : root.statusModel[volumeRow.modelData]
                onMoved: function(value) { root.sendVolume({action: "volume", channel: volumeRow.modelData, value: Math.round(value)}, false) }
                onReleased: function(value) { root.sendVolume({action: "volume", channel: volumeRow.modelData, value: Math.round(value)}, true) }
              }
              PlainText {
                width: Style.space(45)
                anchors.verticalCenter: parent.verticalCenter
                color: root.foreground
                horizontalAlignment: Text.AlignRight
                text: strings.t("percent").arg(root.number(gameVolume.dragging ? Math.round(gameVolume.liveValue) : root.statusModel[volumeRow.modelData]))
                wrapMode: Text.NoWrap
              }
            }
          }
          PlainText {
            width: parent.width
            visible: !!root.statusModel.followWheel && !!root.statusModel.wheelAvailable
            color: root.foreground
            opacity: 0.65
            text: strings.t("wheelLevels").arg(root.number(root.statusModel.effectiveGame)).arg(root.number(root.statusModel.effectiveChat))
          }
          Dropdown {
            id: outputPicker
            width: parent.width
            label: strings.t("fallbackLabel")
            value: root.statusModel.fallback || ""
            options: Model.outputOptions(root.statusModel.outputs || [], root.statusModel.fallback || "",
              strings.t("fallbackNone"), strings.t("unavailableOutput"), strings.t("hdmiOutput"))
            foreground: root.foreground
            fontFamily: root.fontFamily
            onChanged: function(value) {
              if (value !== root.statusModel.fallback) root.send({action: "fallback", value: value})
            }
          }
          Binding {
            target: outputPicker
            property: "value"
            value: root.statusModel.fallback || ""
            when: !outputPicker.popupOpen
          }
          Row {
            width: parent.width
            spacing: Style.space(8)
            visible: !!root.statusModel.fallbackActive && root.statusModel.online !== true
            PlainText {
              width: Style.space(48)
              anchors.verticalCenter: parent.verticalCenter
              text: strings.t("outputVolume")
              color: root.foreground
              wrapMode: Text.NoWrap
              elide: Text.ElideRight
            }
            PanelSlider {
              id: outputVolume
              width: parent.width - Style.space(48 + 45 + 16)
              bar: root.bar
              minimum: 0
              maximum: 100
              integer: true
              step: 5
              enabled: !!root.statusModel.fallbackAvailable && root.statusModel.fallbackVolume !== null
                && root.statusModel.fallbackVolume !== undefined
              value: root.statusModel.fallbackVolume === null || root.statusModel.fallbackVolume === undefined
                ? 0 : Math.min(100, root.statusModel.fallbackVolume)
              onMoved: function(value) {
                root.sendVolume({action: "fallbackVolume", output: root.statusModel.fallback, value: Math.round(value)}, false)
              }
              onReleased: function(value) {
                root.sendVolume({action: "fallbackVolume", output: root.statusModel.fallback, value: Math.round(value)}, true)
              }
            }
            PlainText {
              width: Style.space(45)
              anchors.verticalCenter: parent.verticalCenter
              text: strings.t("percent").arg(root.number(outputVolume.dragging ? Math.round(outputVolume.liveValue) : root.statusModel.fallbackVolume))
              color: root.foreground
              horizontalAlignment: Text.AlignRight
              wrapMode: Text.NoWrap
            }
          }
          PlainText {
            width: parent.width
            visible: (!!root.statusModel.fallback && !root.statusModel.fallbackAvailable) || !!root.statusModel.fallbackActive
            text: root.statusModel.fallbackActive ? strings.t("shortFallbackActive") : strings.t("fallbackMissing")
            color: root.statusModel.fallbackActive ? root.foreground : root.bar ? root.bar.urgent : Color.urgent
          }
          PanelSeparator { width: parent.width; foreground: root.foreground }
          Row {
            width: parent.width
            spacing: Style.space(8)
            Button {
              width: root.hasSettings ? (parent.width - parent.spacing) / 2 : parent.width
              text: strings.tn("appCount", root.appCount).arg(Commons.I18n.formatNumber(root.appCount))
              iconText: root.appsExpanded ? "−" : "+"
              selected: root.appsExpanded
              foreground: root.foreground
              focusable: true
              onClicked: root.appsExpanded = !root.appsExpanded
            }
            Button {
              width: (parent.width - parent.spacing) / 2
              visible: root.hasSettings
              text: strings.t("moreSettings")
              iconText: root.moreExpanded ? "−" : "+"
              selected: root.moreExpanded
              foreground: root.foreground
              focusable: true
              onClicked: root.moreExpanded = !root.moreExpanded
            }
          }
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.appsExpanded
            PlainText {
              width: parent.width
              text: strings.t(root.appCount ? "appHelp" : "noApps")
              color: root.foreground
              opacity: 0.75
            }
            Repeater {
              model: root.statusModel.apps || []
              Row {
                id: appRow
                required property var modelData
                width: bodyColumn.width
                spacing: Style.space(8)
                PlainText {
                  width: parent.width - appRoute.implicitWidth - parent.spacing
                  anchors.verticalCenter: parent.verticalCenter
                  text: appRow.modelData.name
                  color: root.foreground
                  maximumLineCount: 2
                  elide: Text.ElideRight
                }
                ButtonGroup {
                  id: appRoute
                  value: appRow.modelData.channel
                  options: [{value: "arctis_game", label: strings.t("game")}, {value: "arctis_chat", label: strings.t("chat")}]
                  foreground: root.foreground
                  enabled: !!root.statusModel.ready
                  onChanged: function(value) { root.send({action: "route", id: appRow.modelData.id, channel: value}) }
                }
              }
            }
            Button {
              width: parent.width
              visible: root.statusModel.ruleCount > 0
              text: strings.t("forget")
              foreground: root.foreground
              focusable: true
              onClicked: root.send({action: "forget"})
            }
          }
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.hasSettings && root.moreExpanded
            Toggle {
              width: parent.width
              visible: !!root.statusModel.supportsWheel
              label: strings.t("followWheel")
              checked: !!root.statusModel.followWheel
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.send({action: "followWheel", value: !root.statusModel.followWheel})
            }
          PlainText { width: parent.width; visible: !!root.statusModel.supportsSidetone; text: strings.t("sidetone"); color: root.foreground }
          Row {
            width: parent.width
            visible: !!root.statusModel.supportsSidetone
            spacing: Style.space(4)
            Repeater {
              model: ["sidetoneOff", "sidetoneLow", "sidetoneMedium", "sidetoneHigh"]
              Button {
                required property int index
                required property string modelData
                width: (parent.width - Style.space(4) * 3) / 4
                text: strings.t(modelData)
                selected: root.statusModel.sidetone === index
                iconText: selected ? "✓" : ""
                foreground: root.foreground
                focusable: true
                enabled: root.canConfigure
                onClicked: root.send({action: "sidetone", value: index, product: root.statusModel.product})
              }
            }
          }
          PlainText { width: parent.width; visible: !!root.statusModel.supportsSleep; text: strings.t("sleep"); color: root.foreground }
          Row {
            width: parent.width
            visible: !!root.statusModel.supportsSleep
            spacing: Style.space(4)
            Repeater {
              model: [0, 15, 30, 60]
              Button {
                required property int modelData
                width: (parent.width - Style.space(4) * 3) / 4
                text: modelData === 0 ? strings.t("never") : strings.t("minutes").arg(Commons.I18n.formatNumber(modelData))
                selected: root.statusModel.sleep === modelData
                iconText: selected ? "✓" : ""
                foreground: root.foreground
                focusable: true
                enabled: root.canConfigure
                onClicked: root.send({action: "sleep", value: modelData, product: root.statusModel.product})
              }
            }
          }
            PlainText {
              width: parent.width
              visible: !!(root.statusModel.supportsSidetone || root.statusModel.supportsSleep)
              text: strings.t("writeOnly")
              color: root.foreground
              opacity: 0.65
            }
            Toggle {
              width: parent.width
              visible: !!root.statusModel.supportsBattery
              label: strings.t("lowBattery")
              checked: !!root.statusModel.alerts
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.send({action: "alerts", value: !root.statusModel.alerts})
            }
          }
        }
      }
    }
  }
}
