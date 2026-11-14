import QtQuick
import Quickshell
import qs.Commons as Commons
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.imanhri.ps5-releases"

  Commons.Translations {
    id: strings
    directory: Qt.resolvedUrl("i18n")
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function refresh() {
    if (panelLoader.item && typeof panelLoader.item.refresh === "function")
      panelLoader.item.refresh()
  }

  function togglePanel() {
    if (panelLoader.item && typeof panelLoader.item.toggle === "function")
      panelLoader.item.toggle()
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item && typeof panelLoader.item.open === "function")
      panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && typeof panelLoader.item.close === "function")
      panelLoader.item.close()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item && typeof panelLoader.item.closeForPopoutSwitch === "function")
      panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  ShellIpc {
    target: root.moduleName

    function refresh(): void { root.broadcast("refresh") }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      Image {
        source: Qt.resolvedUrl("ps5-logo.svg")
        sourceSize: Qt.size(48, 48)
        width: 32
        height: 32
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
      }
    }
    slotSize: Style.bar.statusSlot
    tooltipText: strings.t("bar.tooltip")

    onPressed: function(mouseButton) {
      if (mouseButton === Qt.MiddleButton) root.refresh()
      else if (mouseButton === Qt.LeftButton) root.togglePanel()
    }
  }
}
