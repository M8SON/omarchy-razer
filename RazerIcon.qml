import QtQuick
import qs.Commons

// A small keyboard silhouette: rounded body with a three-column key grid and a
// spacebar. `glow` tints the keys with the device's current colour so the bar
// icon reflects what the keyboard is actually doing.
Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Color.foreground
  property color glow: color
  property bool lit: true

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  readonly property real bodyW: iconSize
  readonly property real bodyH: iconSize * 0.72
  readonly property real keySize: Math.max(1, iconSize * 0.13)
  readonly property real keyGap: Math.max(1, iconSize * 0.075)

  Rectangle {
    id: body
    anchors.centerIn: parent
    width: root.bodyW
    height: root.bodyH
    radius: Math.max(2, root.iconSize * 0.12)
    color: "transparent"
    border.color: root.color
    border.width: Math.max(1, root.iconSize * 0.075)
  }

  Column {
    anchors.centerIn: body
    spacing: root.keyGap

    Repeater {
      model: 2
      Row {
        spacing: root.keyGap
        Repeater {
          model: 3
          Rectangle {
            width: root.keySize
            height: root.keySize
            radius: Math.max(1, root.keySize * 0.25)
            color: root.lit ? root.glow : root.color
            opacity: root.lit ? 1.0 : 0.45
          }
        }
      }
    }

    Rectangle {
      width: root.keySize * 3 + root.keyGap * 2
      height: root.keySize
      radius: Math.max(1, root.keySize * 0.25)
      color: root.lit ? root.glow : root.color
      opacity: root.lit ? 1.0 : 0.45
    }
  }
}
