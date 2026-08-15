from __future__ import annotations

import re
import time

import serial
from serial.tools import list_ports

# This file is used to support a RP2040 with a infineon tle493d w2b6 connected to it.
# Future sensors will have to have the same class structure and functions to work properly with the orca hand experiments.
# class sensor() #is used to call the 
# 	def report()
# 	def connect()
# 	def read()
# 	def close() 

SENSOR_LINE_RE = re.compile(
	r"X:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+"
	r"Y:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+"
	r"Z:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+"
	r"T:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)


def resolve_serial_sensor_port(explicit_port: str | None = None) -> str:
	"""Resolve the serial port used by the RP2040 sensor."""
	if explicit_port:
		return explicit_port

	ports = list(list_ports.comports())
	if len(ports) == 1:
		return ports[0].device

	if not ports:
		raise RuntimeError("No serial ports were found. Connect the sensor and try again, or pass a port explicitly.")

	available = ", ".join(port.device for port in ports)
	raise RuntimeError(
		"More than one serial port was found. Pass the sensor port explicitly. "
		f"Available ports: {available}"
	)


def connect(
	port: str | None = None,
	baud: int = 9600,
	*,
	timeout: float = 0.0,
	startup_delay_s: float = 1.5,
) -> serial.Serial:
	"""Open the serial sensor connection and allow the board to reset."""
	resolved_port = resolve_serial_sensor_port(port)
	ser = serial.Serial(resolved_port, baud, timeout=timeout)
	time.sleep(startup_delay_s)
	return ser


def parse_sensor_line(raw_line: str) -> dict[str, float] | None:
	"""Parse one sensor line into numeric X/Y/Z/T values."""
	match = SENSOR_LINE_RE.search(raw_line)
	if not match:
		return None

	x, y, z, t = map(float, match.groups())
	return {"x": x, "y": y, "z": z, "t": t}


def read_sensor_sample(sensor: serial.Serial) -> dict[str, float] | None:
	"""Drain available serial lines and return the latest valid sample."""
	if not sensor.is_open:
		raise ValueError("The serial sensor connection is closed.")

	latest_sample: dict[str, float] | None = None
	while sensor.in_waiting:
		raw = sensor.readline().decode(errors="ignore").strip()
		sample = parse_sensor_line(raw)
		if sample is not None:
			latest_sample = sample

	return latest_sample


def close_serial_sensor(sensor: serial.Serial) -> None:
	"""Close the serial sensor connection if it is still open."""
	if sensor.is_open:
		sensor.close()