from __future__ import annotations

import argparse
import re
import sys
import time
from collections import deque

import serial
from serial.tools import list_ports
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from pyqtgraph.Qt import QtCore, QtWidgets


LINE_RE = re.compile(
    r"X:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+"
    r"Y:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+"
    r"Z:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+"
    r"T:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)


class PlotWindow(gl.GLViewWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle("RP2040 3D Vector Plot (Esc to quit)")

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            QtWidgets.QApplication.quit()
        else:
            super().keyPressEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live 3D serial vector plotter for RP2040 output.")
    parser.add_argument("--port", default=None, help="Serial port, e.g. COM4. If omitted, the only connected serial device is used.")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--window", type=int, default=500, help="Number of samples kept in view")
    parser.add_argument("--interval-ms", type=int, default=20, help="GUI update interval in ms")
    parser.add_argument("--scale", type=float, default=1.0, help="Optional scale factor applied to X/Y/Z")
    parser.add_argument("--axis-size", type=float, default=10.0, help="Size of the 3D axes/grid")
    parser.add_argument("--camera-distance", type=float, default=16.0, help="Camera distance from origin")
    parser.add_argument("--camera-elevation", type=float, default=28.0, help="Camera elevation angle")
    parser.add_argument("--camera-azimuth", type=float, default=45.0, help="Camera azimuth angle")
    return parser.parse_args()


def resolve_serial_port(explicit_port: str | None) -> str:
    if explicit_port:
        return explicit_port

    ports = list(list_ports.comports())
    if len(ports) == 1:
        return ports[0].device

    if not ports:
        raise RuntimeError("No serial ports were found. Connect the device and try again, or pass --port explicitly.")

    available = ", ".join(port.device for port in ports)
    raise RuntimeError(
        "More than one serial port was found. Pass --port explicitly to choose one. "
        f"Available ports: {available}"
    )


def main() -> None:
    args = parse_args()

    port = resolve_serial_port(args.port)

    ser = serial.Serial(port, args.baud, timeout=0)
    time.sleep(1.5)

    app = QtWidgets.QApplication(sys.argv)
    win = PlotWindow()
    win.setGeometry(100, 100, 1100, 900)
    win.show()
    win.setCameraPosition(
        distance=args.camera_distance,
        elevation=args.camera_elevation,
        azimuth=args.camera_azimuth,
    )

    # 3D axes/grid for context
    axis = gl.GLAxisItem()
    axis.setSize(x=args.axis_size, y=args.axis_size, z=args.axis_size)
    win.addItem(axis)

    grid = gl.GLGridItem()
    grid.setSize(args.axis_size, args.axis_size)
    grid.setSpacing(1, 1)
    win.addItem(grid)

    origin = gl.GLScatterPlotItem(pos=[[0, 0, 0]], color=(1, 1, 1, 1), size=8)
    win.addItem(origin)

    vector_line = gl.GLLinePlotItem(pos=[[0, 0, 0], [0, 0, 0]], color=(1, 0, 0, 1), width=3, antialias=True)
    win.addItem(vector_line)

    trail = gl.GLLinePlotItem(pos=[[0, 0, 0]], color=(0, 1, 1, 0.8), width=2, antialias=True, mode="line_strip")
    win.addItem(trail)

    point = gl.GLScatterPlotItem(pos=[[0, 0, 0]], color=(1, 1, 0, 1), size=10)
    win.addItem(point)

    maxlen = args.window
    trail_points: deque[list[float]] = deque(maxlen=maxlen)
    latest_point = [0.0, 0.0, 0.0]
    baseline_point: list[float] | None = None

    def update() -> None:
        nonlocal latest_point, baseline_point

        updated = False
        while ser.in_waiting:
            raw = ser.readline().decode(errors="ignore").strip()
            match = LINE_RE.search(raw)
            if not match:
                continue

            x, y, z, _t = map(float, match.groups())
            current_point = [args.scale * x, args.scale * y, args.scale * z]

            if baseline_point is None:
                baseline_point = current_point
                latest_point = [0.0, 0.0, 0.0]
            else:
                latest_point = [
                    current_point[0] - baseline_point[0],
                    current_point[1] - baseline_point[1],
                    current_point[2] - baseline_point[2],
                ]

            trail_points.append(latest_point.copy())
            updated = True

        if not updated and not trail_points:
            return

        vector_line.setData(pos=[[0, 0, 0], latest_point])
        point.setData(pos=[latest_point], size=10, color=(1, 1, 0, 1))
        if len(trail_points) >= 1:
            trail.setData(pos=list(trail_points))

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(args.interval_ms)

    def cleanup() -> None:
        timer.stop()
        if ser.is_open:
            ser.close()

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()