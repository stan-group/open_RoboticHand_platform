"""Quick NI-DAQmx test for an NI USB-6008 using PyQtGraph.

What this script does:
1. Lists detected NI devices.
2. Reads a single analog voltage sample from the first selected channel.
3. Shows a live PyQtGraph plot for multiple analog channels by default.
4. Keeps running until Escape is pressed.

Examples:
    python nidaqmx_test.py
    python nidaqmx_test.py --no-live-plot
    python nidaqmx_test.py --channels ai0 ai1 ai2 ai3 --rate 30
    python nidaqmx_test.py --device Dev1 --channels Dev1/ai0 Dev1/ai1
"""

from __future__ import annotations

import argparse
import os
import time
from collections import deque

import archive.nidaqmx as nidaqmx

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import pyqtgraph as pg
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.system import System
from pyqtgraph.Qt import QtCore, QtWidgets


ESCAPE_KEY = getattr(QtCore.Qt, "Key_Escape", None)
if ESCAPE_KEY is None:
    ESCAPE_KEY = getattr(getattr(QtCore.Qt, "Key", QtCore.Qt), "Key_Escape")

STRONG_FOCUS = getattr(QtCore.Qt, "StrongFocus", None)
if STRONG_FOCUS is None:
    STRONG_FOCUS = getattr(getattr(QtCore.Qt, "FocusPolicy", QtCore.Qt), "StrongFocus")


_LIVE_OBJECTS: list[object] = []


def list_devices() -> list:
    system = System.local()
    devices = list(system.devices)

    print("Detected NI devices:")
    if not devices:
        print("  (none found)")
        return devices

    for device in devices:
        print(f"  - {device.name} ({device.product_type})")
        try:
            ai_channels = list(device.ai_physical_chans)
            if ai_channels:
                print("    AI channels:")
                for chan in ai_channels:
                    print(f"      - {chan.name}")
        except Exception:
            pass

    return devices


def normalize_channels(channel_tokens: list[str]) -> list[str]:
    channels: list[str] = []
    for token in channel_tokens:
        parts = [part.strip() for part in token.split(",")]
        channels.extend(part for part in parts if part)
    return channels


def resolve_channels(device_name: str | None, channels: list[str], devices: list) -> list[str]:
    if not channels:
        channels = ["ai0", "ai1", "ai2", "ai3"]

    if device_name:
        base_device = device_name
    elif devices:
        base_device = devices[0].name
    else:
        raise RuntimeError("No NI device found, so no default channel can be chosen.")

    resolved: list[str] = []
    for channel in channels:
        if "/" in channel:
            resolved.append(channel)
        else:
            resolved.append(f"{base_device}/{channel}")
    return resolved


def read_single_sample(physical_channel: str, min_val: float, max_val: float) -> float:
    with nidaqmx.Task() as task:
        task.ai_channels.add_ai_voltage_chan(
            physical_channel,
            min_val=min_val,
            max_val=max_val,
            terminal_config=TerminalConfiguration.RSE,
        )
        value = task.read(timeout=5.0)
    return float(value)


class LivePlotWindow(QtWidgets.QWidget):
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == ESCAPE_KEY:
            self.close()
            QtWidgets.QApplication.quit()
            return
        super().keyPressEvent(event)


def live_plot(channels: list[str], rate_hz: float, min_val: float, max_val: float, seconds: float | None) -> None:
    history_seconds = 15.0
    history_samples = max(50, int(rate_hz * history_seconds))

    # Averaging window for displayed values (seconds)
    smoothing_seconds = 0.5
    smoothing_samples = max(1, int(rate_hz * smoothing_seconds))

    times = deque(maxlen=history_samples)
    channel_values = [deque(maxlen=history_samples) for _ in channels]
    start_time = time.time()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pg.setConfigOptions(antialias=True)
    window = LivePlotWindow()
    window.setWindowTitle("NI USB-6008 Live Readout")
    window.setFocusPolicy(STRONG_FOCUS)
    window.resize(1100, 700)

    # Layout: plot on top, live-values table underneath
    layout = QtWidgets.QVBoxLayout(window)
    plot_widget = pg.PlotWidget(title="Analog Inputs")
    plot_widget.setLabel("left", "Voltage", units="V")
    plot_widget.setLabel("bottom", "Time", units="s")
    plot_widget.showGrid(x=True, y=True, alpha=0.3)
    plot_widget.addLegend()
    layout.addWidget(plot_widget)

    # Table to display latest values for each channel
    table = QtWidgets.QTableWidget(len(channels), 2, parent=window)
    table.setHorizontalHeaderLabels(["Channel", "Value (V)"])
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
    for i, ch in enumerate(channels):
        table.setItem(i, 0, QtWidgets.QTableWidgetItem(ch))
        table.setItem(i, 1, QtWidgets.QTableWidgetItem("---"))
    layout.addWidget(table)

    window.show()
    window.activateWindow()
    window.raise_()
    window.setFocus()

    curves = []
    for index, channel in enumerate(channels):
        pen = pg.mkPen(color=pg.intColor(index, hues=max(len(channels), 4)), width=2)
        curves.append(plot_widget.plot([], [], pen=pen, name=channel))

    timer = QtCore.QTimer(window)
    interval_ms = max(1, int(1000.0 / max(rate_hz, 1.0)))

    _LIVE_OBJECTS[:] = [app, window, timer, table]

    with nidaqmx.Task() as task:
        for channel in channels:
            task.ai_channels.add_ai_voltage_chan(
                channel,
                min_val=min_val,
                max_val=max_val,
                terminal_config=TerminalConfiguration.RSE,
            )

        task.timing.cfg_samp_clk_timing(
            rate_hz,
            sample_mode=AcquisitionType.CONTINUOUS,
            samps_per_chan=max(100, int(rate_hz)),
        )
        task.start()

        if seconds and seconds > 0:
            print(f"Starting live plot for {seconds:.1f} s at {rate_hz:.1f} Hz on: {', '.join(channels)}")
        else:
            print(f"Starting live plot at {rate_hz:.1f} Hz on: {', '.join(channels)}")
            print("Press Escape to stop.")

        def update_plot() -> None:
            elapsed = time.time() - start_time
            if seconds and seconds > 0 and elapsed >= seconds:
                timer.stop()
                app.quit()
                return

            try:
                available = int(task.in_stream.avail_samp_per_chan)
                if available <= 0:
                    return

                sample = task.read(
                    number_of_samples_per_channel=available,
                    timeout=max(0.1, 1.0 / max(rate_hz, 1.0)),
                )
            except Exception as exc:
                print(f"Live plot read failed: {exc}")
                timer.stop()
                app.quit()
                return

            if isinstance(sample, list):
                if sample and isinstance(sample[0], list):
                    values = [float(channel_samples[-1]) if channel_samples else float("nan") for channel_samples in sample]
                else:
                    values = [float(value) for value in sample]
            else:
                values = [float(sample)]

            if len(values) < len(channels):
                values.extend([float("nan")] * (len(channels) - len(values)))
            elif len(values) > len(channels):
                values = values[: len(channels)]

            times.append(elapsed)
            for index, value in enumerate(values):
                channel_values[index].append(value)

            # Update live-values table with averaged value over the last second
            for i in range(len(channels)):
                vals = list(channel_values[i])
                if not vals:
                    display_text = "---"
                else:
                    # compute average over last smoothing_samples (or fewer if not available)
                    recent = vals[-smoothing_samples:]
                    try:
                        avg = sum(recent) / len(recent)
                        display_text = f"{avg:.6f}"
                    except Exception:
                        display_text = str(recent[-1])
                table.setItem(i, 1, QtWidgets.QTableWidgetItem(display_text))

            x_data = list(times)
            for index, curve in enumerate(curves):
                curve.setData(x_data, list(channel_values[index]))

        timer.timeout.connect(update_plot)
        timer.start(interval_ms)
        app.exec()

    _LIVE_OBJECTS.clear()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test NI USB-6008 readout using NI-DAQmx.")
    parser.add_argument("--device", help="NI device name, for example Dev1")
    parser.add_argument(
        "--channels",
        nargs="+",
        default=["ai0", "ai1", "ai2", "ai3"],
        help="Analog input channels to plot/read, e.g. ai0 ai1 ai2 ai3 or Dev1/ai0 Dev1/ai1",
    )
    parser.add_argument("--min", dest="min_val", type=float, default=-10.0, help="Minimum expected voltage")
    parser.add_argument("--max", dest="max_val", type=float, default=10.0, help="Maximum expected voltage")
    parser.add_argument(
        "--live-plot",
        dest="live_plot",
        action="store_true",
        default=True,
        help="Show a live PyQtGraph graph for all selected channels (default)",
    )
    parser.add_argument(
        "--no-live-plot",
        dest="live_plot",
        action="store_false",
        help="Disable live plotting and only do the single-sample read",
    )
    parser.add_argument("--rate", type=float, default=30.0, help="Live plotting sample rate in Hz")
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="Optional duration of the live plot in seconds; 0 means run until Escape is pressed",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    devices = list_devices()
    if not devices:
        print("No NI device detected. Check the USB connection and NI-DAQmx installation.")
        return 1

    channels = resolve_channels(args.device, normalize_channels(args.channels), devices)
    print(f"Using channels: {', '.join(channels)}")

    if args.live_plot:
        try:
            live_plot(channels, args.rate, args.min_val, args.max_val, args.seconds)
        except Exception as exc:
            print(f"Live plot failed: {exc}")
            return 3

        return 0

    try:
        value = read_single_sample(channels[0], args.min_val, args.max_val)
    except Exception as exc:
        print(f"Single-sample read failed: {exc}")
        return 2

    print(f"Single sample read ({channels[0]}): {value:.6f} V")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())