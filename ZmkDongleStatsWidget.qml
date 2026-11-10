import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Services
import qs.DCommon.Widgets as DCommon
import qs.Modules.Plugins

PluginComponent {
    id: root

    pluginService: PluginService
    readonly property int refreshMinutes: 10
    readonly property int lowLevel: 20
    property var sides: []
    property string error: "reading..."
    property var updatedAt: null
    property string deviceName: ""
    property var devices: []
    // USB product name picked in the popout, empty means the first that answers
    readonly property string selectedDevice: pluginData?.device ?? ""

    function selectDevice(name) {
        if (pluginService)
            pluginService.savePluginData(pluginId, "device", name);
        if (pluginData)
            pluginData.device = name;
        refresh();
    }

    // Outlines traced from photos and tilted 5 degrees, as fractions of each half's width; matched against the USB name
    readonly property var shapes: ({
        corne: { left: [[0.0,0.1],[0.038,0.502],[0.438,0.466],[0.526,0.565],[0.796,0.575],[0.913,0.626],[1.0,0.447],[0.96,0.013],[0.817,0.026],[0.809,0.01],[0.678,0.021],[0.669,0.004],[0.538,0.015],[0.533,0.0],[0.408,0.017],[0.397,0.028],[0.273,0.039],[0.268,0.072]],
            right: [[1.0,0.103],[0.733,0.076],[0.73,0.041],[0.6,0.029],[0.596,0.012],[0.471,0.0],[0.463,0.015],[0.333,0.002],[0.325,0.019],[0.195,0.007],[0.186,0.021],[0.043,0.008],[0.0,0.441],[0.085,0.619],[0.203,0.571],[0.472,0.562],[0.562,0.465],[0.958,0.503]],
            aspect: 0.627, thick: { left: [0.709,0.295], right: [0.291,0.292] } },
        sofle: { left: [[0.805,0.0],[0.451,0.029],[0.307,0.061],[0.0,0.176],[0.08,0.66],[0.308,0.701],[0.67,0.63],[0.912,0.666],[0.942,0.591],[0.935,0.507],[0.916,0.489]],
            right: [[0.218,0.0],[0.054,0.483],[0.0,0.497],[0.016,0.651],[0.184,0.631],[0.318,0.641],[0.617,0.742],[0.873,0.727],[1.0,0.253],[0.58,0.067]],
            aspect: 0.742, thick: { left: [0.479,0.347], right: [0.439,0.353] } },
        totem: { left: [[0.04,0.078],[0.073,0.447],[0.0,0.469],[0.04,0.619],[0.086,0.611],[0.097,0.652],[0.672,0.615],[0.932,0.688],[0.985,0.545],[0.947,0.527],[0.888,0.0],[0.55,0.028],[0.528,0.0],[0.367,0.015],[0.366,0.045]],
            right: [[0.957,0.078],[0.624,0.046],[0.623,0.015],[0.463,0.0],[0.447,0.029],[0.098,0.003],[0.044,0.535],[0.0,0.564],[0.06,0.695],[0.198,0.64],[0.315,0.62],[0.898,0.661],[0.911,0.619],[0.959,0.629],[1.0,0.477],[0.925,0.454]],
            aspect: 0.695, thick: { left: [0.501,0.322], right: [0.498,0.327] } },
        lily58: { left: [[0.0,0.124],[0.056,0.649],[0.352,0.633],[0.365,0.705],[0.389,0.725],[0.773,0.693],[0.864,0.734],[0.891,0.725],[0.973,0.549],[0.997,0.431],[0.954,0.0],[0.261,0.055],[0.067,0.089]],
            right: [[0.034,0.0],[0.0,0.431],[0.025,0.549],[0.109,0.724],[0.227,0.686],[0.608,0.719],[0.631,0.703],[0.646,0.626],[0.946,0.639],[1.0,0.123],[0.987,0.104],[0.677,0.041]],
            aspect: 0.734, thick: { left: [0.597,0.368], right: [0.411,0.363] } }
    })
    readonly property var shape: {
        const name = deviceName.toLowerCase().replace(/[^a-z0-9]/g, "");
        const key = Object.keys(shapes).find(k => name.includes(k));
        return shapes[key || "corne"];
    }

    function levelOf(label) {
        const side = sides.find(s => s.label === label);
        return side ? side.level : -1;
    }

    onInteractionActiveChanged: {
        if (interactionActive)
            refresh();
    }

    function refresh() {
        if (!reader.running)
            reader.running = true;
    }

    Process {
        id: reader
        readonly property string dir: Qt.resolvedUrl(".").toString().replace("file://", "")
        command: ["python3", "-I", dir + "zmk_battery.py"].concat(root.selectedDevice ? ["--device", root.selectedDevice] : [])
        stdout: SplitParser {
            onRead: line => {
                try {
                    const result = JSON.parse(line);
                    root.devices = result.devices || [];
                    // Keep the last levels when the port is busy, e.g. with ZMK Studio open
                    if (result.ok) {
                        root.sides = result.sides;
                        root.error = "";
                        root.updatedAt = new Date();
                        root.deviceName = result.name || "";
                    } else {
                        root.error = result.error || "unknown error";
                    }
                } catch (e) {
                    root.error = "bad output: " + line;
                }
            }
        }
    }

    Timer {
        interval: root.refreshMinutes * 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingS

            DCommon.DIcon {
                name: "keyboard"
                size: Theme.iconSize - 6
                color: root.sides.length > 0 ? Theme.surfaceText : Theme.surfaceVariantText
                anchors.verticalCenter: parent.verticalCenter
            }

            StyledText {
                visible: root.sides.length === 0
                text: "--"
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceVariantText
                anchors.verticalCenter: parent.verticalCenter
            }

            Repeater {
                model: root.sides

                StyledText {
                    required property var modelData
                    text: modelData.label + " " + modelData.level + "%"
                    font.pixelSize: Theme.fontSizeSmall + 2
                    color: modelData.level <= root.lowLevel ? Theme.error : Theme.surfaceText
                    opacity: root.error === "" ? 1.0 : 0.6
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: 0

            DCommon.DIcon {
                name: "keyboard"
                size: Theme.iconSize - 6
                color: root.sides.length > 0 ? Theme.surfaceText : Theme.surfaceVariantText
                anchors.horizontalCenter: parent.horizontalCenter
            }

            Repeater {
                model: root.sides

                StyledText {
                    required property var modelData
                    text: modelData.label + modelData.level
                    font.pixelSize: Theme.fontSizeSmall + 2
                    color: modelData.level <= root.lowLevel ? Theme.error : Theme.surfaceText
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }
        }
    }

    popoutWidth: 380

    popoutContent: Component {
        PopoutComponent {
            width: root.popoutWidth
            headerText: root.deviceName || "Keyboard"
            detailsText: reader.running ? "Reading the dongle..."
                : root.error !== "" ? root.error
                : root.updatedAt ? "Updated " + Qt.formatTime(root.updatedAt, "hh:mm") : ""

            headerActions: Component {
                DCommon.DActionButton {
                    iconName: "refresh"
                    tooltipText: "Read again"
                    enabled: !reader.running
                    onClicked: root.refresh()
                }
            }

            // Only shown when more than one ZMK keyboard with Studio is plugged in
            Flow {
                visible: root.devices.length > 1
                width: parent.width
                leftPadding: Theme.spacingS
                spacing: Theme.spacingXS

                Repeater {
                    model: root.devices

                    Rectangle {
                        required property string modelData
                        readonly property bool active: modelData === (root.selectedDevice || root.deviceName)

                        width: chipText.implicitWidth + Theme.spacingM * 2
                        height: chipText.implicitHeight + Theme.spacingXS * 2
                        radius: height / 2
                        color: active ? Theme.primary : Theme.surfaceContainerHigh

                        StyledText {
                            id: chipText
                            anchors.centerIn: parent
                            text: parent.modelData
                            font.pixelSize: Theme.fontSizeSmall
                            color: parent.active ? Theme.primaryText : Theme.surfaceText
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            enabled: !reader.running
                            onClicked: root.selectDevice(parent.modelData)
                        }
                    }
                }
            }

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Theme.spacingL
                topPadding: Theme.spacingS
                bottomPadding: Theme.spacingM

                HalfSilhouette {
                    label: "Left"
                    shape: root.shape
                    level: root.levelOf("L")
                    lowLevel: root.lowLevel
                    dimmed: root.error !== ""
                }

                HalfSilhouette {
                    label: "Right"
                    shape: root.shape
                    mirrored: true
                    level: root.levelOf("R")
                    lowLevel: root.lowLevel
                    dimmed: root.error !== ""
                }
            }
        }
    }

    // Outline of one keyboard half, red when its battery is low
    component HalfSilhouette: Item {
        id: half

        property string label: ""
        property int level: -1
        property bool mirrored: false
        property bool dimmed: false
        property int lowLevel: 20

        readonly property bool known: level >= 0
        readonly property bool low: known && level <= lowLevel

        property var shape: null
        readonly property var outline: shape ? (mirrored ? shape.right : shape.left) : []
        readonly property real aspect: shape ? shape.aspect : 0.6
        // Center of the largest circle that fits inside the outline
        readonly property var thickest: shape ? (mirrored ? shape.thick.right : shape.thick.left) : [0.5, 0.3]

        implicitWidth: 160
        implicitHeight: implicitWidth * aspect

        onLowChanged: canvas.requestPaint()
        onMirroredChanged: canvas.requestPaint()
        onOutlineChanged: canvas.requestPaint()

        Canvas {
            id: canvas
            anchors.fill: parent
            opacity: half.dimmed || !half.known ? 0.6 : 1.0

            onPaint: {
                const ctx = getContext("2d");
                const pad = 2;
                const s = Math.min(width - pad * 2, (height - pad * 2) / half.aspect);
                ctx.reset();

                // Rounded corners: each radius is capped at half of its shorter edge
                const pts = half.outline.map(p => [pad + p[0] * s, pad + p[1] * s]);
                const radius = 0.03 * s;
                const n = pts.length;
                const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
                const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
                ctx.beginPath();
                const start = mid(pts[n - 1], pts[0]);
                ctx.moveTo(start[0], start[1]);
                for (let i = 0; i < n; i++) {
                    const prev = pts[(i + n - 1) % n], cur = pts[i], next = pts[(i + 1) % n];
                    const r = Math.min(radius, dist(prev, cur) / 2, dist(cur, next) / 2);
                    ctx.arcTo(cur[0], cur[1], next[0], next[1], r);
                }
                ctx.closePath();

                ctx.fillStyle = half.low ? Qt.tint(Theme.surfaceContainerHigh, Qt.alpha(Theme.error, 0.3)) : Theme.surfaceContainerHigh;
                ctx.fill();
                ctx.lineWidth = 2;
                ctx.lineJoin = "round";
                ctx.strokeStyle = half.low ? Theme.error : Theme.outline;
                ctx.stroke();
            }
        }

        Column {
            x: half.thickest[0] * half.width - width / 2
            y: half.thickest[1] * half.width - height / 2

            StyledText {
                anchors.horizontalCenter: parent.horizontalCenter
                text: half.known ? half.level + "%" : "--"
                font.pixelSize: Theme.fontSizeXLarge + 8
                font.weight: Font.Bold
                color: half.low ? Theme.error : Theme.surfaceText
            }

            StyledText {
                anchors.horizontalCenter: parent.horizontalCenter
                text: half.label
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceVariantText
            }
        }
    }
}
