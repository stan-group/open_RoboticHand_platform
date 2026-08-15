import argparse
import re
import sys
import time
from collections import deque

import serial
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets


LINE_RE = re.compile(
    r"X:\s*([+-]?\d+(?:\.\d+)?)\s+Y:\s*([+-]?\d+(?:\.\d+)?)\s+Z:\s*([+-]?\d+(?:\.\d+)?)\s+T:\s*([+-]?\d+(?:\.\d+)?)"
)


class PlotWindow(pg.GraphicsLayoutWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle("RP2040 TLx493D Live Plot (Esc to quit)")

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            QtWidgets.QApplication.quit()
        else:
            super().keyPressEvent(event)


def parse_args():
    p = argparse.ArgumentParser(description="Live serial plotter for RP2040 output using pyqtgraph.")
    p.add_argument("--port", required=True, help="Serial port, e.g. COM5")
    p.add_argument("--baud", type=int, default=9600, help="Baud rate (default: 115200)")
    p.add_argument("--window", type=int, default=50, help="Number of samples kept in view")
    p.add_argument("--interval-ms", type=int, default=20, help="GUI update interval in ms")
    return p.parse_args()


def main():
    args = parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0)
    time.sleep(1.5)  # allow board reset on serial open

    app = QtWidgets.QApplication(sys.argv)
    win = PlotWindow(show=True, size=(1000, 600))

    plot = win.addPlot(title="Magnetic Field / Temperature")
    plot.showGrid(x=True, y=True)
    plot.addLegend()
    plot.setLabel("left", "Value")
    plot.setLabel("bottom", "Sample")

    maxlen = args.window
    xs = deque(maxlen=maxlen)
    ys = deque(maxlen=maxlen)
    zs = deque(maxlen=maxlen)
    ts = deque(maxlen=maxlen)
    idx = deque(maxlen=maxlen)

    curve_x = plot.plot(pen=pg.mkPen("r", width=2), name="X")
    curve_y = plot.plot(pen=pg.mkPen("g", width=2), name="Y")
    curve_z = plot.plot(pen=pg.mkPen("b", width=2), name="Z")
    curve_t = plot.plot(pen=pg.mkPen("y", width=2), name="T")

    sample_i = 0

    def update():
        nonlocal sample_i

        while ser.in_waiting:
            raw = ser.readline().decode(errors="ignore").strip()
            m = LINE_RE.search(raw)
            if not m:
                continue

            x, y, z, t = map(float, m.groups())
            sample_i += 1
            idx.append(sample_i)
            xs.append(x)
            ys.append(y)
            zs.append(z)
            ts.append(t)

        if idx:
            x_axis = list(idx)
            curve_x.setData(x_axis, list(xs))
            curve_y.setData(x_axis, list(ys))
            curve_z.setData(x_axis, list(zs))
            curve_t.setData(x_axis, list(ts))

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(args.interval_ms)

    def cleanup():
        timer.stop()
        if ser.is_open:
            ser.close()

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()