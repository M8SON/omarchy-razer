import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Razer lighting control for the Omarchy bar.
//
// All device state comes from razerctl.py, which talks to openrazer-daemon over
// D-Bus. Raw sysfs is not used anywhere: on the Huntsman V3 Pro Mini
// matrix_brightness and device_mode accept writes, report success and change
// nothing, while the daemon reports the truth.
Panel {
  id: root
  moduleName: "daedalus.razer"
  ipcTarget: "daedalus.razer"

  // The bar sizes each widget slot from these (Bar.qml: activeItem.implicitWidth);
  // without them the slot is 0x0 and the icon never renders.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  property var devices: []
  property string errorText: ""
  property bool loading: false
  property string selectedSerial: ""
  // Colour is held as HSV so the wheel and the value slider each drive one axis
  // without fighting over a single packed colour property.
  property bool colorSeeded: false
  property real hueValue: 0.0
  property real satValue: 1.0
  property real valValue: 1.0
  readonly property color chosenColor: Qt.hsva(hueValue, satValue, valValue, 1)
  // Slider position while dragging, so the UI stays smooth even though every
  // apply is a subprocess round-trip. -1 means "use the device's value".
  property real pendingBrightness: -1
  property real pendingDpi: -1

  property bool serverDesired: false
  property bool serverReady: false
  property int serverFailures: 0
  property var outbox: []

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property int refreshIntervalSec: Math.max(5, Math.min(600, setting("refreshIntervalSec", 30)))

  // Bar.qml's injectProps() only sets bar/moduleName/settings on a bar widget --
  // `manifest` (and its __sourceDir) is never injected, and `omarchyPath` is a
  // first-party global. Resolving against this file's own URL is the only way a
  // third-party bar widget can locate a bundled helper.
  readonly property string helperPath: {
    var url = String(Qt.resolvedUrl("razerctl.py"))
    if (url.indexOf("file://") === 0) url = url.substring(7)
    return decodeURIComponent(url)
  }

  // A device earns a row if anything about it is controllable -- lighting on an
  // RGB keyboard or mouse, or DPI/poll rate on a mouse with no RGB at all (the
  // wired DeathAdder V3 reports zero lighting capabilities but full DPI ones).
  readonly property var controlDevices: {
    var out = []
    for (var i = 0; i < devices.length; i++) {
      var d = devices[i]
      if (!d) continue
      var lights = d.effects && d.effects.length > 0
      if (lights || d.dpi || d.poll) out.push(d)
    }
    return out
  }

  readonly property var current: {
    if (controlDevices.length === 0) return null
    for (var i = 0; i < controlDevices.length; i++)
      if (controlDevices[i].serial === selectedSerial) return controlDevices[i]
    return controlDevices[0]
  }

  readonly property bool hasLighting: current !== null && current.effects && current.effects.length > 0
  readonly property bool hasBrightness: hasLighting && current.brightness !== null && current.brightness !== undefined
  readonly property real brightnessValue: pendingBrightness >= 0 ? pendingBrightness : (hasBrightness ? current.brightness : 0)
  readonly property string currentEffect: current ? String(current.effect || "") : ""
  readonly property bool effectTakesColor: hasLighting && colorEffects.indexOf(currentEffect) !== -1
  readonly property bool anyLight: hasLighting && (hasBrightness ? brightnessValue > 0 && currentEffect !== "none" : currentEffect !== "none")

  readonly property var dpiInfo: current && current.dpi ? current.dpi : null
  readonly property var pollInfo: current && current.poll ? current.poll : null
  readonly property var dpiStages: current && current.dpiStages ? current.dpiStages : []
  readonly property bool hasPerformance: dpiInfo !== null || pollInfo !== null
  readonly property real dpiValue: pendingDpi >= 0 ? pendingDpi : (dpiInfo ? dpiInfo.x : 0)

  readonly property var colorEffects: ["static", "breath", "reactive", "starlight"]
  readonly property var effectLabels: ({
    "spectrum": "Spectrum",
    "static": "Static",
    "breath": "Breath",
    "wave": "Wave",
    "reactive": "Reactive",
    "starlight": "Starlight",
    "none": "Off"
  })

  // The helper is held open rather than spawned per command. Importing
  // openrazer costs ~92ms while the D-Bus write costs ~4ms, so a process per
  // colour sample capped the wheel at ~9 updates/sec and felt like the colour
  // lagging behind the pointer. Held open, a sample round-trips in ~3ms.
  function ensureServer() {
    idleTimer.stop()
    serverDesired = true
    startServer()
  }

  function startServer() {
    if (helperPath === "" || server.running) return
    serverReady = false
    server.running = true
  }

  function stopServer() {
    serverDesired = false
    restartTimer.stop()
    serverReady = false
    outbox = []
    if (server.running) server.running = false
  }

  function send(argv) {
    if (helperPath === "") return
    ensureServer()
    if (!serverReady) {
      // Queued until the warm-up line arrives; a burst of list requests during
      // startup collapses to one.
      if (argv[0] === "list" && outbox.some(function(q) { return q[0] === "list" })) return
      outbox.push(argv)
      return
    }
    server.write(JSON.stringify(argv) + "\n")
  }

  function refresh() {
    loading = true
    send(["list"])
  }

  function applyBrightness(value) {
    if (!current || !hasBrightness || helperPath === "") return
    runAction(["brightness", current.serial, String(Math.round(value))])
  }

  function applyEffect(name) {
    if (!current || helperPath === "") return
    var args = ["effect", current.serial, name]
    if (colorEffects.indexOf(name) !== -1) {
      var c = chosenColor
      args.push(String(Math.round(c.r * 255)))
      args.push(String(Math.round(c.g * 255)))
      args.push(String(Math.round(c.b * 255)))
    }
    runAction(args)
  }

  function applyDpi(value) {
    if (!current || !dpiInfo || helperPath === "") return
    runAction(["dpi", current.serial, String(Math.round(value))])
  }

  function applyPollRate(hz) {
    if (!current || !pollInfo || helperPath === "") return
    runAction(["pollrate", current.serial, String(hz)])
  }

  // Only re-apply when the live effect actually consumes a colour; moving the
  // wheel under Spectrum would otherwise silently do nothing.
  function commitColor() {
    if (effectTakesColor) applyEffect(currentEffect)
  }

  function runAction(args) {
    errorText = ""
    send(args)
  }

  function handleResponse(line) {
    var payload
    try {
      payload = JSON.parse(String(line || "").trim())
    } catch (e) {
      return
    }

    if (payload.cmd === "ready") {
      serverReady = true
      serverFailures = 0
      if (!payload.ok && payload.error) errorText = String(payload.error)
      var queued = outbox
      outbox = []
      for (var i = 0; i < queued.length; i++) send(queued[i])
      return
    }

    if (!payload.ok) {
      errorText = payload.error ? String(payload.error) : "Command failed"
      loading = false
      return
    }
    errorText = ""

    if (payload.cmd === "list") {
      applyDevices(payload.devices || [])
      loading = false
      return
    }

    // The daemon restores persisted state asynchronously and can land after our
    // write, so read the truth back a beat later rather than trusting the echo.
    settleTimer.restart()
  }

  // Open the wheel on the colour the device is actually showing, once, using
  // the value the helper persisted when it was applied. Only on first load --
  // re-seeding later would yank the wheel out from under a drag.
  function seedColor() {
    if (colorSeeded || !current || !current.color || current.color.length !== 3) return
    var c = Qt.rgba(current.color[0] / 255, current.color[1] / 255, current.color[2] / 255, 1)
    if (c.hsvHue >= 0) hueValue = c.hsvHue      // -1 for greys, which carry no hue
    satValue = c.hsvSaturation
    valValue = c.hsvValue
    colorSeeded = true
  }

  function hexOf(c) {
    function two(v) {
      var s = Math.round(v * 255).toString(16).toUpperCase()
      return s.length < 2 ? "0" + s : s
    }
    return "#" + two(c.r) + two(c.g) + two(c.b)
  }

  function supports(name) {
    return current !== null && current.effects.indexOf(name) !== -1
  }

  function applyDevices(list) {
    devices = list
    // Open on something that actually has lighting -- landing on a no-RGB mouse
    // hides the whole point of the plugin behind a device switcher.
    if (selectedSerial === "" && controlDevices.length > 0) {
      var pick = controlDevices[0]
      for (var j = 0; j < controlDevices.length; j++) {
        if (controlDevices[j].effects && controlDevices[j].effects.length > 0) {
          pick = controlDevices[j]
          break
        }
      }
      selectedSerial = pick.serial
    }
    // After selectedSerial, never before: `current` falls back to the first
    // device until it is set, so seeding early reads a no-RGB mouse and bails.
    seedColor()
    // A drag that finished before this refresh landed would otherwise snap the
    // knob back to the pre-drag value.
    if (!brightnessSlider.dragging) pendingBrightness = -1
    if (!dpiSlider.dragging) pendingDpi = -1
  }

  onOpenedChanged: {
    if (opened) {
      serverFailures = 0
      ensureServer()
      refresh()
    } else {
      // Nothing needs the helper with the panel shut; the bar icon keeps
      // rendering from the last list. 32MB is not worth holding idle.
      idleTimer.restart()
    }
  }

  // `running` is assigned imperatively and never bound. Quickshell writes
  // Process.running itself when the child exits, and an imperative write from
  // C++ destroys a QML binding permanently -- so a `running: someFlag` binding
  // works exactly once, then the helper can never be started again and every
  // command silently queues forever.
  Process {
    id: server
    command: root.helperPath === "" ? [] : ["python3", root.helperPath, "serve"]
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.handleResponse(line) } }
    stderr: SplitParser {
      onRead: function(line) {
        var text = String(line || "").trim()
        if (text !== "") root.errorText = text
      }
    }
    onExited: {
      root.serverReady = false
      root.loading = false
      if (root.serverDesired && root.serverFailures < 3) {
        root.serverFailures++
        restartTimer.restart()
        return
      }
      if (root.outbox.length > 0) {
        root.outbox = []
        if (root.errorText === "") root.errorText = "Razer helper stopped"
      }
    }
  }

  Timer {
    id: restartTimer
    interval: 400
    repeat: false
    onTriggered: root.startServer()
  }

  Timer {
    id: idleTimer
    interval: 45000
    repeat: false
    onTriggered: root.stopServer()
  }

  Timer {
    id: settleTimer
    interval: 250
    repeat: false
    onTriggered: root.refresh()
  }

  // A high-polling-rate mouse can emit far faster than 60Hz; one frame of
  // coalescing is enough now that a round-trip is ~3ms.
  Timer {
    id: brightnessTimer
    interval: 16
    repeat: false
    onTriggered: root.applyBrightness(root.pendingBrightness)
  }

  Timer {
    id: dpiTimer
    interval: 16
    repeat: false
    onTriggered: root.applyDpi(root.pendingDpi)
  }

  Timer {
    id: colorTimer
    interval: 16
    repeat: false
    onTriggered: root.commitColor()
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: root.opened
    onTriggered: root.refresh()
  }

  Component.onCompleted: {
    refresh()
    idleTimer.restart()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      Item {
        RazerIcon {
          anchors.centerIn: parent
          iconSize: Style.space(11)
          color: root.barForeground
          glow: root.effectTakesColor ? root.chosenColor : root.barForeground
          lit: root.anyLight
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refresh()
        else if (t === "o" || t === "O") root.applyEffect("none")
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: root.current ? root.current.name : "Razer lighting"
            meta: {
              if (root.errorText !== "") return root.errorText
              if (root.current === null) return root.loading ? "Looking for devices…" : "No Razer devices found"
              if (!root.hasLighting) {
                // A mouse with no RGB still has something worth summarising.
                var bits = []
                if (root.dpiInfo) bits.push(Math.round(root.dpiValue) + " DPI")
                if (root.pollInfo) bits.push(root.pollInfo.current + " Hz")
                return bits.length > 0 ? bits.join(" · ") : "No lighting on this device"
              }
              var label = root.effectLabels[root.currentEffect] || root.currentEffect
              return root.hasBrightness ? label + " · " + Math.round(root.brightnessValue) + "%" : label
            }
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.anyLight ? 1.0 : 0.5
            iconComponent: Component {
              RazerIcon {
                iconSize: Style.font.display
                color: root.foreground
                glow: root.effectTakesColor ? root.chosenColor : root.foreground
                lit: root.anyLight
              }
            }
          }

          // Device switcher, only worth showing when there is a choice.
          Row {
            width: parent.width
            spacing: Style.spacing.controlGap
            visible: root.controlDevices.length > 1

            Repeater {
              model: root.controlDevices
              Button {
                required property var modelData
                text: modelData.name
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.caption
                selected: root.current && root.current.serial === modelData.serial
                onClicked: {
                  root.selectedSerial = modelData.serial
                  root.pendingBrightness = -1
                  root.colorSeeded = false
                  root.seedColor()
                }
              }
            }
          }

          PanelSeparator { width: parent.width; visible: root.current !== null }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            visible: root.hasBrightness

            PanelSectionHeader {
              width: parent.width
              text: "Brightness"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            PanelSlider {
              id: brightnessSlider
              bar: root.bar
              width: parent.width
              minimum: 0
              maximum: 100
              step: 1
              integer: true
              value: root.brightnessValue
              onMoved: function(v) {
                root.pendingBrightness = v
                brightnessTimer.restart()
              }
              onReleased: function(v) {
                root.pendingBrightness = v
                brightnessTimer.stop()
                root.applyBrightness(v)
              }
            }
          }

          PanelSeparator { width: parent.width; visible: root.current !== null }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            visible: root.hasLighting

            PanelSectionHeader {
              width: parent.width
              text: "Effect"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Grid {
              width: parent.width
              columns: 3
              spacing: Style.spacing.controlGap

              Repeater {
                model: ["spectrum", "static", "breath", "wave", "reactive", "starlight", "none"]
                Button {
                  required property var modelData
                  visible: root.supports(modelData)
                  text: root.effectLabels[modelData] || modelData
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  selected: root.currentEffect === modelData
                  onClicked: root.applyEffect(modelData)
                }
              }
            }
          }

          PanelSeparator { width: parent.width; visible: root.effectTakesColor }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            visible: root.effectTakesColor

            PanelSectionHeader {
              width: parent.width
              text: "Colour"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            ColorWheel {
              id: wheel
              anchors.horizontalCenter: parent.horizontalCenter
              hue: root.hueValue
              saturation: root.satValue
              value: root.valValue
              foreground: root.foreground
              onMoved: function(h, s) {
                root.hueValue = h
                root.satValue = s
                colorTimer.restart()
              }
              onCommitted: {
                colorTimer.stop()
                root.commitColor()
              }
            }

            PanelSlider {
              bar: root.bar
              width: parent.width
              minimum: 0
              maximum: 1
              step: 0.01
              value: root.valValue
              onMoved: function(v) {
                root.valValue = v
                colorTimer.restart()
              }
              onReleased: function(v) {
                root.valValue = v
                colorTimer.stop()
                root.commitColor()
              }
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              Rectangle {
                width: Style.space(16)
                height: Style.space(16)
                radius: Style.space(3)
                color: root.chosenColor
                border.width: 1
                border.color: Qt.darker(root.foreground, 1.8)
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                text: root.hexOf(root.chosenColor)
                color: Qt.darker(root.foreground, 1.4)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                anchors.verticalCenter: parent.verticalCenter
              }
            }
          }

          PanelSeparator { width: parent.width; visible: root.hasPerformance && root.hasLighting }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            visible: root.dpiInfo !== null

            PanelSectionHeader {
              width: parent.width
              text: "Sensitivity · " + Math.round(root.dpiValue) + " DPI"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            PanelSlider {
              id: dpiSlider
              bar: root.bar
              width: parent.width
              minimum: 100
              maximum: root.dpiInfo ? root.dpiInfo.max : 1600
              step: 100
              integer: true
              value: root.dpiValue
              onMoved: function(v) {
                root.pendingDpi = v
                dpiTimer.restart()
              }
              onReleased: function(v) {
                root.pendingDpi = v
                dpiTimer.stop()
                root.applyDpi(v)
              }
            }

            // The device's own stage presets -- far more useful than hunting for
            // a round number on a slider that runs to 30000.
            Row {
              width: parent.width
              spacing: Style.spacing.controlGap
              visible: root.dpiStages.length > 0

              Repeater {
                model: root.dpiStages
                Button {
                  required property var modelData
                  text: String(modelData)
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  selected: Math.round(root.dpiValue) === modelData
                  onClicked: {
                    root.pendingDpi = modelData
                    root.applyDpi(modelData)
                  }
                }
              }
            }
          }

          PanelSeparator { width: parent.width; visible: root.pollInfo !== null && root.dpiInfo !== null }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            visible: root.pollInfo !== null

            PanelSectionHeader {
              width: parent.width
              text: "Polling rate"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Grid {
              width: parent.width
              columns: 3
              spacing: Style.spacing.controlGap

              Repeater {
                model: root.pollInfo ? root.pollInfo.options : []
                Button {
                  required property var modelData
                  text: modelData + " Hz"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  selected: root.pollInfo && root.pollInfo.current === modelData
                  onClicked: root.applyPollRate(modelData)
                }
              }
            }
          }
        }
      }
    }
  }
}
