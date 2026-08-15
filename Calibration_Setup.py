from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import archive.nidaqmx as nidaqmx
from nidaqmx.constants import TerminalConfiguration

try:
    import pyqtgraph as pg
    import pyqtgraph.opengl as gl
    from pyqtgraph.Qt import QtCore, QtWidgets
except Exception:  # pragma: no cover - optional GUI dependency
    pg = None
    gl = None
    QtCore = None
    QtWidgets = None

def resolve_channels(device_name: str, channels: Iterable[str]) -> list[str]:
    resolved: list[str] = []
    for channel in channels:
        channel = channel.strip()
        if not channel:
            continue
        if "/" in channel:
            resolved.append(channel)
        else:
            resolved.append(f"{device_name}/{channel}")
    if not resolved:
        raise ValueError("At least one channel must be provided.")
    return resolved


def read_usb6008_voltages(
    channels: Iterable[str],
    device_name: str = "Dev1",
    min_val: float = -10.0,
    max_val: float = 10.0,
    timeout: float = 5.0,
) -> dict[str, float]:
    """Read one voltage sample from each requested NI USB-6008 analog input channel."""

    resolved_channels = resolve_channels(device_name, channels)

    with nidaqmx.Task() as task:
        for channel in resolved_channels:
            task.ai_channels.add_ai_voltage_chan(
                channel,
                min_val=min_val,
                max_val=max_val,
                terminal_config=TerminalConfiguration.RSE,
            )

        values = task.read(timeout=timeout)

    if isinstance(values, list):
        return {channel: float(value) for channel, value in zip(resolved_channels, values, strict=False)}

    return {resolved_channels[0]: float(values)}

@dataclass(frozen=True)
class CalibrationParameters:
    """Tunable parameters for joystick force reconstruction.

    """
    #gains for scaling raw sensor voltages
    sensor_0_gain: float = 1.0
    sensor_1_gain: float = 1.0
    sensor_2_gain: float = 1.0

    #biases for raw sensor voltages
    sensor_0_bias: float = 0.0
    sensor_1_bias: float = 0.0
    sensor_2_bias: float = 0.0

    #biases for inaccuracies in actual sensor angle mounting, sensor 0 is alligned with the x-axis
    angle_1_bias: float = 0.0
    angle_2_bias: float = 0.0

    #Gains for converting sensor forces to tip forces
    sensor_0_moment_gain: float = 1.0
    sensor_1_moment_gain: float = 1.0
    sensor_2_moment_gain: float = 1.0
    # length from base to tip used to convert moments to transverse force
    joystick_length: float = 1.0


def reconstruct_tip_force(sensor_output: dict[str, float], params: CalibrationParameters = CalibrationParameters(),
) -> dict[str, float]:
    """Reconstruct joystick tip force from the three base sensor forces.

    The function expects exactly three sensor readings. Their names can be any
    labels, but the order of values is used together with the configured sensor
    angles.
    """

    if len(sensor_output) != 3:
        raise ValueError("Exactly three base sensor outputs are required for reconstruction.")

    sensor_output = list(sensor_output.values())
    angles = [0, math.radians(120 + params.angle_1_bias), math.radians(240 + params.angle_2_bias)]
    
    forces = [
        params.sensor_0_gain * sensor_output[0] + params.sensor_0_bias,
        params.sensor_1_gain * sensor_output[1] + params.sensor_1_bias,
        params.sensor_2_gain * sensor_output[2] + params.sensor_2_bias,
    ]

    axial_force = sum(forces)

    gains = [params.sensor_0_moment_gain, params.sensor_1_moment_gain, params.sensor_2_moment_gain]

    moment_x = sum(math.sin(angle) * force * gain for angle, force, gain in zip(angles, forces, gains))
    moment_y = sum(math.cos(angle) * force * gain for angle, force, gain in zip(angles, forces, gains))

    transverse_x = moment_x / params.joystick_length
    transverse_y = moment_y / params.joystick_length

    magnitude = math.sqrt(axial_force**2 + transverse_x**2 + transverse_y**2)

    return {
        "axial_force": axial_force,
        "transverse_x": transverse_x,
        "transverse_y": transverse_y,
        "magnitude": magnitude,
        "moment_x": moment_x,
        "moment_y": moment_y,
    }


def read_calibration_sample(
    device_name: str,
    base_sensor_channels: Iterable[str],
    reference_channel: str,
    params: CalibrationParameters = CalibrationParameters(),
    min_val: float = -10.0,
    max_val: float = 10.0,
    timeout: float = 5.0,
    reference_force_scale: float = 1.0,
    reference_force_offset: float = 0.0,
) -> dict[str, float]:
    """Read one synchronized calibration sample.

    Returns a flat dictionary containing the raw base sensor voltages, the raw
    reference sensor voltage, the reconstructed force features, and a converted
    reference force value.
    """

    resolved_base_channels = resolve_channels(device_name, base_sensor_channels)
    resolved_reference_channel = resolve_channels(device_name, [reference_channel])[0]
    all_channels = [*resolved_base_channels, resolved_reference_channel]

    with nidaqmx.Task() as task:
        for channel in all_channels:
            task.ai_channels.add_ai_voltage_chan(
                channel,
                min_val=min_val,
                max_val=max_val,
                terminal_config=TerminalConfiguration.RSE,
            )

        values = task.read(timeout=timeout)

    if not isinstance(values, list):
        values = [float(values)]

    if len(values) != len(all_channels):
        raise RuntimeError("DAQ returned an unexpected number of channel values.")

    base_values = values[:3]
    reference_voltage = float(values[3])
    base_forces = {channel: float(value) for channel, value in zip(resolved_base_channels, base_values, strict=False)}
    reconstructed = reconstruct_tip_force(base_forces, params)
    reference_force = reference_force_scale * reference_voltage + reference_force_offset

    sample = {
        "timestamp_s": time.time(),
        "reference_voltage": reference_voltage,
        "reference_force": reference_force,
    }
    for channel, value in base_forces.items():
        sample[f"{channel}_voltage"] = value
    sample.update(reconstructed)
    return sample


def record_z_load(
    known_axial_force: float,
    device_name: str,
    sensor_channels: Iterable[str],
    duration_s: float = 5.0,
    sample_rate_hz: float = 20.0,
    output_dir: str | Path = "Calibration Data",
    min_val: float = -10.0,
    max_val: float = 10.0,
    timeout: float = 5.0,
) -> Path:
    """Record three-sensor axial calibration data to an auto-incremented CSV.

    The CSV is saved as `Axial_001.csv`, `Axial_002.csv`, ... in `output_dir`.
    """

    if duration_s <= 0:
        raise ValueError("duration_s must be positive.")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive.")

    resolved_channels = resolve_channels(device_name, sensor_channels)
    if len(resolved_channels) != 3:
        raise ValueError("Exactly three sensor channels are required.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_index = 1
    while True:
        candidate = output_path / f"Axial_{file_index:03d}.csv"
        if not candidate.exists():
            csv_path = candidate
            break
        file_index += 1

    rows: list[dict[str, float]] = []
    start_time = time.time()
    next_sample_time = start_time
    sample_period = 1.0 / sample_rate_hz

    while True:
        now = time.time()
        elapsed = now - start_time
        if elapsed >= duration_s:
            break

        if now < next_sample_time:
            time.sleep(min(sample_period, next_sample_time - now))
            continue

        voltages = read_usb6008_voltages(
            channels=resolved_channels,
            device_name=device_name,
            min_val=min_val,
            max_val=max_val,
            timeout=timeout,
        )

        sample = {
            "timestamp_s": now,
            "elapsed_s": elapsed,
            "known_axial_force": float(known_axial_force),
        }
        for idx, (channel, value) in enumerate(voltages.items()):
            sample[f"Sensor{idx}_voltage"] = float(value)

        rows.append(sample)
        next_sample_time += sample_period

    if not rows:
        raise RuntimeError("No z-calibration samples were recorded.")

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path


def record_gimbal_force(
    device_name: str,
    sensor_channels: Iterable[str],
    load_cell_channel: str,
    duration_s: float = 5.0,
    sample_rate_hz: float = 20.0,
    output_dir: str | Path = "Calibration Data",
    load_cell_force_scale: float = 1.0,
    load_cell_force_offset: float = 0.0,
    min_val: float = -10.0,
    max_val: float = 10.0,
    timeout: float = 5.0,
) -> Path:
    """Record four sensor channels while a calibrated load cell applies force.

    The CSV is saved as `Gimbal001.csv`, `Gimbal002.csv`, ... in
    `output_dir`.
    """

    if duration_s <= 0:
        raise ValueError("duration_s must be positive.")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive.")

    resolved_sensor_channels = resolve_channels(device_name, sensor_channels)
    if len(resolved_sensor_channels) != 3:
        raise ValueError("Exactly three sensor channels are required.")

    resolved_load_cell_channel = resolve_channels(device_name, [load_cell_channel])[0]
    all_channels = [*resolved_sensor_channels, resolved_load_cell_channel]

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_index = 1
    while True:
        candidate = output_path / f"Gimbal{file_index:03d}.csv"
        if not candidate.exists():
            csv_path = candidate
            break
        file_index += 1

    rows: list[dict[str, float]] = []
    start_time = time.time()
    next_sample_time = start_time
    sample_period = 1.0 / sample_rate_hz

    while True:
        now = time.time()
        elapsed = now - start_time
        if elapsed >= duration_s:
            break

        if now < next_sample_time:
            time.sleep(min(sample_period, next_sample_time - now))
            continue

        readings = read_usb6008_voltages(
            channels=all_channels,
            device_name=device_name,
            min_val=min_val,
            max_val=max_val,
            timeout=timeout,
        )

        load_cell_voltage = float(readings[resolved_load_cell_channel])
        load_cell_force = load_cell_force_scale * load_cell_voltage + load_cell_force_offset

        sample: dict[str, float] = {
            "timestamp_s": now,
            "elapsed_s": elapsed,
            "load_cell_voltage": load_cell_voltage,
            "load_cell_force": load_cell_force,
        }
        for idx, channel in enumerate(resolved_sensor_channels):
            sample[f"Sensor{idx}_voltage"] = float(readings[channel])

        rows.append(sample)
        next_sample_time += sample_period

    if not rows:
        raise RuntimeError("No calibrated sensor samples were recorded.")

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path


def calibrate_on_data(
    input_dir: str | Path = "Calibration Data",
) -> dict[str, float | CalibrationParameters]:
    """Calibrate the sensor and moment parameters from axial and gimbal CSV files.

    This function does two stages:
    1. Axial files (`Axial_*.csv`) fit the three sensor gains and a shared bias.
    2. Gimbal files (`Gimbal*.csv`) fit one shared moment gain for all three
       sensors, assuming the moment gains are equal to each other for a rough
       first-pass calibration.

    The axial stage keeps the sensor gains and biases fixed for the gimbal stage.
    Only the shared moment gain scale is estimated from the gimbal data.
    """

    input_path = Path(input_dir)
    if not input_path.exists():
        raise ValueError(f"Input directory {input_path} does not exist.")

    def read_voltage(row: dict[str, str], index: int) -> float:
        return float(row[f"Sensor{index}_voltage"])

    axial_files = sorted(input_path.glob("Axial_*.csv"))
    if not axial_files:
        raise ValueError(f"No Axial_*.csv files found in {input_path}")

    axial_voltages: list[tuple[float, float, float]] = []
    axial_forces: list[float] = []

    for csv_file in axial_files:
        with csv_file.open("r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    axial_voltages.append((read_voltage(row, 0), read_voltage(row, 1), read_voltage(row, 2)))
                    axial_forces.append(float(row["known_axial_force"]))
                except (KeyError, ValueError):
                    continue

    if not axial_voltages:
        raise RuntimeError("No valid calibration data found in Axial CSV files.")

    axial_matrix = np.column_stack(
        [
            [sample[0] for sample in axial_voltages],
            [sample[1] for sample in axial_voltages],
            [sample[2] for sample in axial_voltages],
            np.ones(len(axial_voltages)),
        ]
    )
    axial_target = np.array(axial_forces)

    axial_coefficients, *_ = np.linalg.lstsq(axial_matrix, axial_target, rcond=None)
    sensor_0_gain, sensor_1_gain, sensor_2_gain, bias_total = axial_coefficients
    sensor_bias = float(bias_total) / 3.0

    axial_predictions = axial_matrix @ axial_coefficients
    axial_residual_std = float(np.sqrt(np.mean((axial_target - axial_predictions) ** 2)))

    gimbal_files = sorted(input_path.glob("Gimbal*.csv"))
    if not gimbal_files:
        raise ValueError(f"No Gimbal*.csv files found in {input_path}")

    gimbal_rows: list[dict[str, float]] = []
    for csv_file in gimbal_files:
        with csv_file.open("r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    v0 = read_voltage(row, 0)
                    v1 = read_voltage(row, 1)
                    v2 = read_voltage(row, 2)
                    load_cell_force = float(row["load_cell_force"])
                except (KeyError, ValueError):
                    continue

                forces = [
                    sensor_0_gain * v0 + sensor_bias,
                    sensor_1_gain * v1 + sensor_bias,
                    sensor_2_gain * v2 + sensor_bias,
                ]
                axial_force = sum(forces)
                angles = [0.0, math.radians(120.0), math.radians(240.0)]
                moment_x_raw = sum(math.sin(angle) * force for angle, force in zip(angles, forces, strict=False))
                moment_y_raw = sum(math.cos(angle) * force for angle, force in zip(angles, forces, strict=False))

                gimbal_rows.append(
                    {
                        "axial_force": axial_force,
                        "moment_xy_sq": moment_x_raw**2 + moment_y_raw**2,
                        "load_cell_force": load_cell_force,
                    }
                )

    if not gimbal_rows:
        raise RuntimeError("No valid calibration data found in Gimbal CSV files.")

    gimbal_axial = np.array([row["axial_force"] for row in gimbal_rows])
    gimbal_moment_xy_sq = np.array([row["moment_xy_sq"] for row in gimbal_rows])
    gimbal_force_sq = np.array([row["load_cell_force"] ** 2 for row in gimbal_rows])

    # Rough model with equal moment gains:
    #   F_tip^2 = axial_force^2 + k * (moment_x_raw^2 + moment_y_raw^2)
    # where k = moment_gain^2.
    y = gimbal_force_sq - gimbal_axial**2
    x = gimbal_moment_xy_sq

    valid_mask = x > 0.0
    if not np.any(valid_mask):
        raise RuntimeError("Gimbal data did not contain enough non-zero moment content to fit a moment gain.")

    x_valid = x[valid_mask]
    y_valid = y[valid_mask]
    moment_gain_sq = float(np.dot(x_valid, y_valid) / np.dot(x_valid, x_valid))
    moment_gain_sq = max(moment_gain_sq, 0.0)
    shared_moment_gain = math.sqrt(moment_gain_sq)

    gimbal_predictions = np.sqrt(np.maximum(gimbal_axial**2 + shared_moment_gain**2 * gimbal_moment_xy_sq, 0.0))
    gimbal_residual_std = float(np.sqrt(np.mean((np.array([row["load_cell_force"] for row in gimbal_rows]) - gimbal_predictions) ** 2)))

    params = CalibrationParameters(
        sensor_0_gain=float(sensor_0_gain),
        sensor_1_gain=float(sensor_1_gain),
        sensor_2_gain=float(sensor_2_gain),
        sensor_0_bias=sensor_bias,
        sensor_1_bias=sensor_bias,
        sensor_2_bias=sensor_bias,
        sensor_0_moment_gain=shared_moment_gain,
        sensor_1_moment_gain=shared_moment_gain,
        sensor_2_moment_gain=shared_moment_gain,
    )

    return {
        "parameters": params,
        "sensor_0_gain": float(sensor_0_gain),
        "sensor_1_gain": float(sensor_1_gain),
        "sensor_2_gain": float(sensor_2_gain),
        "sensor_bias": sensor_bias,
        "shared_moment_gain": shared_moment_gain,
        "axial_residual_std": axial_residual_std,
        "gimbal_residual_std": gimbal_residual_std,
        "n_axial_samples": len(axial_voltages),
        "n_gimbal_samples": len(gimbal_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record joystick force calibration data using an NI USB-6008.")
    parser.add_argument("--device", default="Dev1", help="NI device name, for example Dev1")
    parser.add_argument("--duration", type=float, default=10.0, help="Recording duration in seconds")
    parser.add_argument("--rate", type=float, default=20.0, help="Logging rate in Hz")
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Calibration command to run")
    
    # Z-load subcommand
    z_load_parser = subparsers.add_parser("z-load", help="Record axial calibration with known force")
    z_load_parser.add_argument("--known-axial-force", type=float, required=True, help="Known axial force applied (N)")
    z_load_parser.add_argument("--sensor-channels", nargs=3, default=["ai0", "ai1", "ai2"], help="Three sensor channels")
    
    # Gimbal / calibrated load cell subcommand
    # Accept both the name 'gimbal' and the historical alias 'calibrated'.
    # Provide sensible defaults for the channels so they don't need to
    # be specified each time (first three are sensors, fourth is load cell).
    cal_parser = subparsers.add_parser("gimbal", aliases=["calibrated"], help="Record with calibrated load cell")
    cal_parser.add_argument("--sensor-channels", nargs=3, default=["ai0", "ai1", "ai2"], help="Three sensor channels (default: ai0 ai1 ai2)")
    cal_parser.add_argument("--load-cell-channel", default="ai3", help="Load cell channel (default: ai3)")
    cal_parser.add_argument("--load-cell-scale", type=float, default=1.0, help="Load cell force scale")
    cal_parser.add_argument("--load-cell-offset", type=float, default=0.0, help="Load cell force offset")

    # Calibrate on existing data
    calibrate_parser = subparsers.add_parser("calibrate", help="Calibrate using all data in the input folder and save results")
    calibrate_parser.add_argument("--input-dir", default="Calibration Data", help="Folder with Axial_*.csv and Gimbal*.csv (default: Calibration Data)")
    calibrate_parser.add_argument("--output-dir", default="Calibration Results", help="Folder to write calibration results JSON files (default: Calibration Results)")
    
    def add_visualise_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        vis_parser = subparsers.add_parser(name, help=help_text)
        vis_parser.add_argument("--device", default="Dev1", help="NI device name, for example Dev1")
        vis_parser.add_argument("--calib-dir", default="Calibration Results", help="Folder with Calibrated_*.json files (default: Calibration Results)")
        vis_parser.add_argument("--input-dir", default="Calibration Data", help="Folder with Gimbal*.csv files (default: Calibration Data)")
        vis_parser.add_argument("--sensor-channels", nargs=3, default=["ai0", "ai1", "ai2"], help="Three sensor channels (default: ai0 ai1 ai2)")
        vis_parser.add_argument("--load-cell-channel", default="ai3", help="Load cell channel (default: ai3)")
        vis_parser.add_argument("--load-cell-scale", type=float, default=1.0, help="Load cell force scale")
        vis_parser.add_argument("--load-cell-offset", type=float, default=0.0, help="Load cell force offset")
        vis_parser.add_argument("--interval-ms", type=int, default=50, help="GUI update interval in ms")
        vis_parser.add_argument("--axis-size", type=float, default=10.0, help="Size of the 3D axes/grid")
        vis_parser.add_argument("--camera-distance", type=float, default=16.0, help="Camera distance from origin")
        vis_parser.add_argument("--camera-elevation", type=float, default=28.0, help="Camera elevation angle")
        vis_parser.add_argument("--camera-azimuth", type=float, default=45.0, help="Camera azimuth angle")
        return vis_parser

    add_visualise_parser("visualise", "Visualise reconstructed tip force from the latest data")
    add_visualise_parser("visualise_gimbal", "Visualise reconstructed tip force, gimbal load cell, and error from latest data")

    return parser.parse_args()


def load_latest_calibration(calib_dir: str | Path) -> CalibrationParameters:
    calib_path = Path(calib_dir)
    calib_files = sorted(calib_path.glob("Calibrated_*.json"))
    if not calib_files:
        raise ValueError(f"No Calibrated_*.json files found in {calib_path}")

    latest_calib = calib_files[-1]
    with latest_calib.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    params_data = payload.get("parameters", {})
    return CalibrationParameters(**params_data)


def reconstruct_tip_force_from_sample(row: dict[str, float], params: CalibrationParameters) -> dict[str, float]:
    sensor_bias = float(params.sensor_0_bias)
    v0 = float(row["Sensor0_voltage"])
    v1 = float(row["Sensor1_voltage"])
    v2 = float(row["Sensor2_voltage"])

    f0 = params.sensor_0_gain * v0 + sensor_bias
    f1 = params.sensor_1_gain * v1 + sensor_bias
    f2 = params.sensor_2_gain * v2 + sensor_bias

    axial = f0 + f1 + f2
    angles = [0.0, math.radians(120.0 + params.angle_1_bias), math.radians(240.0 + params.angle_2_bias)]
    gains = [params.sensor_0_moment_gain, params.sensor_1_moment_gain, params.sensor_2_moment_gain]
    moment_x = sum(math.sin(angle) * force * gain for angle, force, gain in zip(angles, [f0, f1, f2], gains, strict=False))
    moment_y = sum(math.cos(angle) * force * gain for angle, force, gain in zip(angles, [f0, f1, f2], gains, strict=False))

    transverse_x = moment_x / params.joystick_length
    transverse_y = moment_y / params.joystick_length
    magnitude = math.sqrt(axial**2 + transverse_x**2 + transverse_y**2)

    return {
        "axial_force": axial,
        "transverse_x": transverse_x,
        "transverse_y": transverse_y,
        "magnitude": magnitude,
        "moment_x": moment_x,
        "moment_y": moment_y,
    }


def run_visualisation(args: argparse.Namespace, show_gimbal: bool) -> None:
    if pg is None or gl is None or QtCore is None or QtWidgets is None:
        raise RuntimeError("pyqtgraph, PyQt, and PyOpenGL are required for visualisation")

    params = load_latest_calibration(args.calib_dir)
    resolved_channels = resolve_channels(args.device, [*args.sensor_channels, args.load_cell_channel])
    if len(resolved_channels) != 4:
        raise ValueError("visualisation requires exactly three sensor channels and one load-cell channel")

    app = QtWidgets.QApplication([])

    class PlotWindow(gl.GLViewWidget):
        def __init__(self) -> None:
            super().__init__()
            title = "Calibration Visualisation - Gimbal" if show_gimbal else "Calibration Visualisation - Tip Force"
            self.setWindowTitle(f"{title} (Esc to quit)")

        def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key_Escape:
                QtWidgets.QApplication.quit()
            else:
                super().keyPressEvent(event)

    win = PlotWindow()
    win.setGeometry(100, 100, 1100, 900)
    win.show()
    win.setCameraPosition(
        distance=args.camera_distance,
        elevation=args.camera_elevation,
        azimuth=args.camera_azimuth,
    )

    axis = gl.GLAxisItem()
    axis.setSize(x=args.axis_size, y=args.axis_size, z=args.axis_size)
    win.addItem(axis)

    grid = gl.GLGridItem()
    grid.setSize(args.axis_size, args.axis_size)
    grid.setSpacing(1, 1)
    win.addItem(grid)

    origin = gl.GLScatterPlotItem(pos=[[0, 0, 0]], color=(1, 1, 1, 1), size=8)
    win.addItem(origin)

    reconstructed_line = gl.GLLinePlotItem(pos=[[0, 0, 0], [0, 0, 0]], color=(1, 0, 0, 1), width=4, antialias=True)
    win.addItem(reconstructed_line)

    gimbal_line = None
    error_line = None
    if show_gimbal:
        gimbal_line = gl.GLLinePlotItem(pos=[[0, 0, 0], [0, 0, 0]], color=(0, 0, 1, 1), width=4, antialias=True)
        error_line = gl.GLLinePlotItem(pos=[[0, 0, 0], [0, 0, 0]], color=(1, 1, 0, 1), width=3, antialias=True)
        win.addItem(gimbal_line)
        win.addItem(error_line)

    trail = gl.GLLinePlotItem(pos=[[0, 0, 0]], color=(0, 1, 1, 0.8), width=2, antialias=True, mode="line_strip")
    win.addItem(trail)
    trail_points: list[list[float]] = []

    overlay = QtWidgets.QLabel(win)
    overlay.setStyleSheet("QLabel { color: white; background-color: rgba(0,0,0,160); padding: 6px; }")
    overlay.setFixedWidth(560)
    overlay.setFixedHeight(130 if show_gimbal else 110)
    overlay.setWordWrap(True)
    overlay.move(10, 10)
    overlay.show()

    latest_tip = [0.0, 0.0, 0.0]
    latest_error = [0.0, 0.0, 0.0]

    def update() -> None:
        nonlocal latest_tip, latest_error

        sample = read_usb6008_voltages(
            channels=resolved_channels,
            device_name=args.device,
            min_val=-10.0,
            max_val=10.0,
            timeout=5.0,
        )

        row = {
            "Sensor0_voltage": sample[resolved_channels[0]],
            "Sensor1_voltage": sample[resolved_channels[1]],
            "Sensor2_voltage": sample[resolved_channels[2]],
            "load_cell_voltage": sample[resolved_channels[3]],
            "load_cell_force": args.load_cell_scale * sample[resolved_channels[3]] + args.load_cell_offset,
        }

        reconstructed = reconstruct_tip_force_from_sample(row, params)
        reconstructed_vec = [reconstructed["transverse_x"], reconstructed["transverse_y"], reconstructed["axial_force"]]
        latest_tip = reconstructed_vec

        reconstructed_line.setData(pos=[[0, 0, 0], latest_tip])

        overlay_lines = [
            f"X force: {reconstructed['transverse_x']:+.3f} N",
            f"Y force: {reconstructed['transverse_y']:+.3f} N",
            f"Z force: {reconstructed['axial_force']:+.3f} N",
            f"Magnitude: {reconstructed['magnitude']:.3f} N",
        ]

        if show_gimbal and gimbal_line is not None and error_line is not None:
            gimbal_vec = [0.0, 0.0, row["load_cell_force"]]
            latest_error = [
                reconstructed_vec[0] - gimbal_vec[0],
                reconstructed_vec[1] - gimbal_vec[1],
                reconstructed_vec[2] - gimbal_vec[2],
            ]
            gimbal_line.setData(pos=[[0, 0, 0], gimbal_vec])
            error_line.setData(pos=[[0, 0, 0], latest_error])

            signed_error = reconstructed["magnitude"] - row["load_cell_force"]
            overlay_lines.extend([
                f"Load cell: {row['load_cell_force']:.3f} N | Error: {signed_error:+.3f} N",
                f"Abs error: {abs(signed_error):.3f} N",
            ])

        trail_points.append(latest_tip.copy())
        if len(trail_points) > 140:
            trail_points.pop(0)
        trail.setData(pos=trail_points)

        overlay_lines.append("Esc quits")
        overlay.setText("\n".join(overlay_lines))

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(args.interval_ms)

    print(f"Starting live visualisation from {args.device} using {args.calib_dir}")

    def cleanup() -> None:
        timer.stop()

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())


def main() -> int:
    args = parse_args()
    
    if args.command == "z-load":
        csv_path = record_z_load(
            known_axial_force=args.known_axial_force,
            device_name=args.device,
            sensor_channels=args.sensor_channels,
            duration_s=args.duration,
            sample_rate_hz=args.rate,
        )
        print(f"Recorded z-load calibration to {csv_path}")
    
    elif args.command == "gimbal":
        csv_path = record_gimbal_force(
            device_name=args.device,
            sensor_channels=args.sensor_channels,
            load_cell_channel=args.load_cell_channel,
            duration_s=args.duration,
            sample_rate_hz=args.rate,
            load_cell_force_scale=args.load_cell_scale,
            load_cell_force_offset=args.load_cell_offset,
        )
        print(f"Recorded gimbal force data to {csv_path}")
    
    elif args.command == "calibrate":
        results = calibrate_on_data(input_dir=args.input_dir)

        out_path = Path(args.output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        file_index = 1
        while True:
            candidate = out_path / f"Calibrated_{file_index:03d}.json"
            if not candidate.exists():
                json_path = candidate
                break
            file_index += 1

        serializable: dict[str, object] = {}
        for key, value in results.items():
            if key == "parameters":
                serializable[key] = asdict(value)
            elif isinstance(value, (np.floating, np.integer)):
                serializable[key] = float(value)
            else:
                serializable[key] = value

        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(serializable, fh, indent=2)

        print(f"Wrote calibration results to {json_path}")

    elif args.command == "visualise":
        run_visualisation(args, show_gimbal=False)

    elif args.command == "visualise_gimbal":
        run_visualisation(args, show_gimbal=True)
        
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
