import QtQuick
import qs.Commons

// HSV colour wheel: hue around the circumference, saturation from centre to
// edge, with value handled by a separate slider so the wheel itself never has
// to repaint while the user changes brightness.
//
// Drawn as 360 gradient wedges rather than per-pixel ImageData -- one paint,
// no visible seams, and fast enough to be imperceptible at this size.
Item {
  id: root

  property real hue: 0          // 0..1
  property real saturation: 1   // 0..1
  property real value: 1        // 0..1, dims the preview only
  property color foreground: Color.foreground

  readonly property color picked: Qt.hsva(hue, saturation, value, 1)
  readonly property real radius: Math.min(width, height) / 2 - Style.space(2)

  signal moved(real h, real s)
  signal committed()

  implicitWidth: Style.space(150)
  implicitHeight: Style.space(150)

  Canvas {
    id: canvas
    anchors.fill: parent
    renderStrategy: Canvas.Threaded

    onPaint: {
      var ctx = getContext("2d")
      var cx = width / 2
      var cy = height / 2
      var r = root.radius
      ctx.reset()
      ctx.clearRect(0, 0, width, height)
      if (r <= 0) return

      var steps = 360
      for (var i = 0; i < steps; i++) {
        var a0 = (i / steps) * 2 * Math.PI
        var a1 = ((i + 1) / steps) * 2 * Math.PI
        var mid = (a0 + a1) / 2

        // Overlap each wedge slightly; exact edges leave hairline seams.
        ctx.beginPath()
        ctx.moveTo(cx, cy)
        ctx.arc(cx, cy, r, a0 - 0.012, a1 + 0.012)
        ctx.closePath()

        var grad = ctx.createLinearGradient(cx, cy, cx + Math.cos(mid) * r, cy + Math.sin(mid) * r)
        grad.addColorStop(0, Qt.hsva(i / steps, 0, 1, 1).toString())
        grad.addColorStop(1, Qt.hsva(i / steps, 1, 1, 1).toString())
        ctx.fillStyle = grad
        ctx.fill()
      }
    }
  }

  // Value is previewed by dimming rather than repainting the wheel.
  Rectangle {
    anchors.centerIn: parent
    width: root.radius * 2
    height: width
    radius: width / 2
    color: "black"
    opacity: 1 - root.value
  }

  // Selection marker.
  Rectangle {
    id: marker
    width: Style.space(12)
    height: width
    radius: width / 2
    color: "transparent"
    border.color: root.foreground
    border.width: Math.max(2, Style.space(2))
    x: parent.width / 2 + Math.cos(root.hue * 2 * Math.PI) * root.saturation * root.radius - width / 2
    y: parent.height / 2 + Math.sin(root.hue * 2 * Math.PI) * root.saturation * root.radius - height / 2

    Rectangle {
      anchors.fill: parent
      anchors.margins: Math.max(1, Style.space(1))
      radius: width / 2
      color: root.picked
    }
  }

  MouseArea {
    id: area
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.CrossCursor

    function apply(mx, my) {
      var dx = mx - root.width / 2
      var dy = my - root.height / 2
      var dist = Math.sqrt(dx * dx + dy * dy)
      var angle = Math.atan2(dy, dx)
      if (angle < 0) angle += 2 * Math.PI
      // Clamped, so a drag that leaves the circle pins to full saturation
      // instead of jumping. Reported, not assigned -- hue/saturation are bound
      // from the owner, and writing them here would sever that binding.
      root.moved(angle / (2 * Math.PI),
                 root.radius > 0 ? Math.min(1, dist / root.radius) : 0)
    }

    onPressed: function(mouse) { apply(mouse.x, mouse.y) }
    onPositionChanged: function(mouse) { if (pressed) apply(mouse.x, mouse.y) }
    onReleased: root.committed()
  }
}
