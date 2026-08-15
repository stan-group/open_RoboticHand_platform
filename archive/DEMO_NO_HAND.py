from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PyQt5 import QtCore, QtWidgets
import math
from serial.tools import list_ports

# Try to import the real OrcaHand if available in the environment. If not,
# we'll fall back to `FakeOrcaHand` so the demo remains runnable.
try:
	from orca_core import OrcaHand  # type: ignore
	REAL_ORCA_AVAILABLE = True
except Exception:
	OrcaHand = None  # type: ignore
	REAL_ORCA_AVAILABLE = False


PROJECT_ROOT = Path(__file__).resolve().parent
# Inlined constants and helper functions from Orca_v1_DEMO.py so this demo
# can run standalone without importing other project modules.
MODEL_PATH = PROJECT_ROOT / "models" / "orcahand_v1_right"
PRESS_CYCLES = 4
MOVE_STEPS = 80
MOVE_STEP_SIZE = 0.01
BOOT_LIFT_STEPS = 60
BOOT_LIFT_STEP_SIZE = 0.01
HOLD_STEPS = 15
HOLD_STEP_SIZE = 0.01
INDEX_PRESS_DELTA = 10
INDEX_LIFT_DELTA = -12


def resolve_hand_port(expected_serial: str = "FTAK89GBA") -> str:
	ports = list(list_ports.comports())
	if not ports:
		raise RuntimeError("No serial ports were detected.")

	for port in ports:
		if getattr(port, "serial_number", None) == expected_serial:
			return port.device

	for port in ports:
		if expected_serial in (port.hwid or ""):
			return port.device

	ftdi_ports = [port for port in ports if "0403:6014" in (port.hwid or "")]
	if len(ftdi_ports) == 1:
		return ftdi_ports[0].device

	if len(ports) == 1:
		return ports[0].device

	available = ", ".join(port.device for port in ports)
	raise RuntimeError(f"Could not auto-detect the Orca hand port. Found: {available}")


def build_pointing_pose(hand=None) -> dict[str, float]:
	return {
		"thumb_mcp": 50,
		"thumb_abd": 42,
		"thumb_pip": 108,
		"thumb_dip": 112,
		"index_abd": 28,
		"index_mcp": 0,
		"index_pip": -20,
		"middle_abd": 37,
		"middle_mcp": 91,
		"middle_pip": 107,
		"ring_abd": 37,
		"ring_mcp": 91,
		"ring_pip": 107,
		"pinky_abd": 37,
		"pinky_mcp": 98,
		"pinky_pip": 108,
		"wrist": -25,
	}


def build_press_pose(base_pose: dict[str, float]) -> dict[str, float]:
	press_pose = dict(base_pose)
	press_pose["index_mcp"] = base_pose["index_mcp"] + INDEX_PRESS_DELTA
	press_pose["index_pip"] = base_pose["index_pip"] + INDEX_PRESS_DELTA
	return press_pose


def build_lift_pose(base_pose: dict[str, float]) -> dict[str, float]:
	lift_pose = dict(base_pose)
	lift_pose["index_mcp"] = base_pose["index_mcp"] + INDEX_LIFT_DELTA
	lift_pose["index_pip"] = base_pose["index_pip"] + INDEX_LIFT_DELTA
	return lift_pose


def smooth_move(hand, target_pose: dict[str, float], *, num_steps: int, step_size: float) -> None:
	hand.set_joint_pos(target_pose, num_steps=num_steps, step_size=step_size)


def press_path(progress: float) -> float:
	return 0.5 - 0.5 * math.cos(math.pi * progress)


# A lightweight fake OrcaHand to avoid hardware dependencies while still
# exercising the demo motion logic. This simulates joint states and accepts
# the same methods used by the demo code.
import threading


class FakeOrcaHand:
	def __init__(self, model_path: str | Path | None = None):
		self.port: str | None = None
		self.max_current = 100
		self.control_mode = 0
		# Motor ids are only used by the original code for torque writes;
		# we simulate a small set of ids so code paths remain compatible.
		self.motor_ids = [1, 2, 3, 4, 5]
		self._motor_lock = threading.Lock()
		self._dxl_client = None
		# Start with the pointing pose
		self._joints = build_pointing_pose(None)

	def connect(self) -> tuple[bool, str]:
		return True, "(Fake) Connected to Orca hand"

	def disconnect(self) -> None:
		return None

	def set_max_current(self, val: float) -> None:
		self.max_current = val

	def _compute_wrap_offsets_dict(self) -> None:
		return None

	def get_joint_pos(self, as_list: bool = False):
		if as_list:
			return list(self._joints.values())
		return dict(self._joints)

	def set_joint_pos(self, target_pose: dict[str, float], *, num_steps: int = 1, step_size: float = 0.0) -> None:
		# Linearly interpolate internal joint state towards target over num_steps
		steps = max(1, int(num_steps))
		for s in range(steps):
			for k, v in target_pose.items():
				current = self._joints.get(k, 0.0)
				delta = (v - current) / (steps - s) if steps - s > 0 else 0.0
				self._joints[k] = current + delta
			# Small sleep to simulate motion time; respect provided step_size but
			# don't sleep too long to keep the demo responsive.
			time.sleep(min(max(step_size, 0.001), 0.05))




SERIAL_SCRIPT = PROJECT_ROOT / "Serial_3D.py"
CALIB_SCRIPT = PROJECT_ROOT / "Calibration_Setup.py"
DEFAULT_DURATION_S = 45.0
HAND_CONNECT_RETRIES = 15
HAND_CONNECT_RETRY_DELAY_S = 0.5

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZE = 0x20000000
WS_MAXIMIZE = 0x01000000
WS_SYSMENU = 0x00080000
GWL_STYLE = -16
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
WM_CLOSE = 0x0010

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
DEMO_MUTEX_NAME = "Global\\OrcaHandCombinedDemoMutex"


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run the combined Orca hand demo with both 3D visualisers.")
	parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S, help="Total demo duration in seconds (default: 45)")
	parser.add_argument("--serial-port", dest="serial_port", default=None, help="Port for Serial_3D.py (e.g. COM4). If omitted, auto-detect")
	parser.add_argument("--serial-baud", type=int, default=115200, help="Baud rate for Serial_3D.py (default: 115200)")
	parser.add_argument("--calibration-command", default="visualise", choices=["visualise", "visualise_gimbal"], help="Calibration visualisation mode to launch")
	parser.add_argument("--wait-for-windows", type=float, default=15.0, help="Seconds to wait for plot windows before embedding them")
	parser.add_argument("--port", dest="hand_port", default=None, help="Orca hand serial port (e.g. COM4). If omitted, auto-detect")
	return parser.parse_args()


def launch_process(command: list[str]) -> subprocess.Popen[str]:
	creationflags = 0
	if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
		creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

	return subprocess.Popen(
		command,
		cwd=str(PROJECT_ROOT),
		creationflags=creationflags,
	)


def acquire_single_instance_mutex() -> int:
	mutex_handle = kernel32.CreateMutexW(None, False, DEMO_MUTEX_NAME)
	if not mutex_handle:
		raise RuntimeError("Unable to create the demo mutex.")

	if kernel32.GetLastError() == 183:
		kernel32.CloseHandle(mutex_handle)
		raise RuntimeError("Another DEMO.py instance is already running. Close it before starting a new one.")

	return mutex_handle


def find_window_by_title_fragment(fragment: str) -> int | None:
	found_hwnd: int | None = None
	enum_windows = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

	def callback(hwnd, _lparam):
		nonlocal found_hwnd
		if not user32.IsWindowVisible(hwnd):
			return True

		length = user32.GetWindowTextLengthW(hwnd)
		if length <= 0:
			return True

		buffer = ctypes.create_unicode_buffer(length + 1)
		user32.GetWindowTextW(hwnd, buffer, length + 1)
		title = buffer.value
		if fragment.lower() in title.lower():
			found_hwnd = hwnd
			return False
		return True

	user32.EnumWindows(enum_windows(callback), 0)
	return found_hwnd


def embed_window(hwnd: int, parent_hwnd: int) -> None:
	style = user32.GetWindowLongW(hwnd, GWL_STYLE)
	style &= ~WS_CAPTION
	style &= ~WS_THICKFRAME
	style &= ~WS_MINIMIZE
	style &= ~WS_MAXIMIZE
	style &= ~WS_SYSMENU
	style |= WS_CHILD | WS_VISIBLE
	user32.SetWindowLongW(hwnd, GWL_STYLE, style)
	user32.SetParent(hwnd, parent_hwnd)
	user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)


def resize_window(hwnd: int, left: int, top: int, width: int, height: int) -> None:
	user32.MoveWindow(hwnd, left, top, width, height, True)


class DemoWindow(QtWidgets.QMainWindow):
	def __init__(self, stop_event: threading.Event) -> None:
		super().__init__()
		self.stop_event = stop_event
		self.setWindowTitle("Orca Combined Demo (Esc to quit)")
		self.resize(1500, 900)

		central = QtWidgets.QWidget(self)
		self.setCentralWidget(central)

		layout = QtWidgets.QVBoxLayout(central)
		layout.setContentsMargins(8, 8, 8, 8)
		layout.setSpacing(8)

		self.status_label = QtWidgets.QLabel("Launching plots and hand motion...", self)
		self.status_label.setStyleSheet("QLabel { color: white; background-color: rgba(0,0,0,180); padding: 6px; }")
		layout.addWidget(self.status_label)

		self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
		layout.addWidget(self.splitter, 1)

		self.calib_host = QtWidgets.QFrame(self)
		self.calib_host.setFrameShape(QtWidgets.QFrame.StyledPanel)
		self.calib_host.setAttribute(QtCore.Qt.WA_NativeWindow, True)
		self.serial_host = QtWidgets.QFrame(self)
		self.serial_host.setFrameShape(QtWidgets.QFrame.StyledPanel)
		self.serial_host.setAttribute(QtCore.Qt.WA_NativeWindow, True)

		self.splitter.addWidget(self.calib_host)
		self.splitter.addWidget(self.serial_host)
		self.splitter.setStretchFactor(0, 1)
		self.splitter.setStretchFactor(1, 1)

		self.processes: dict[str, subprocess.Popen[str]] = {}
		self.embedded_hwnds: dict[str, int] = {}
		self.window_timer = QtCore.QTimer(self)
		self.window_timer.timeout.connect(self.poll_child_windows)
		self.window_timer.start(300)

		self.process_timer = QtCore.QTimer(self)
		self.process_timer.timeout.connect(self.poll_child_processes)
		self.process_timer.start(300)

	def keyPressEvent(self, event):
		if event.key() == QtCore.Qt.Key_Escape:
			self.request_stop("Esc pressed in demo controller")
			return
		super().keyPressEvent(event)

	def request_stop(self, reason: str) -> None:
		self.status_label.setText(reason)
		self.stop_event.set()
		self.close_embedded_windows()
		QtCore.QTimer.singleShot(0, QtWidgets.QApplication.quit)

	def register_processes(self, calibration_process: subprocess.Popen[str], serial_process: subprocess.Popen[str]) -> None:
		self.processes = {
			"calibration": calibration_process,
			"serial": serial_process,
		}

	def terminate_child_processes(self) -> None:
		for process in self.processes.values():
			if process.poll() is None:
				process.terminate()

	def close_embedded_windows(self) -> None:
		for hwnd in self.embedded_hwnds.values():
			if user32.IsWindow(hwnd):
				user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

	def poll_child_windows(self) -> None:
		if self.stop_event.is_set():
			return

		mapping = {
			"calibration": ("Calibration Visualisation", self.calib_host),
			"serial": ("RP2040 3D Vector Plot", self.serial_host),
		}

		for key, (fragment, host_widget) in mapping.items():
			if key in self.embedded_hwnds:
				continue
			hwnd = find_window_by_title_fragment(fragment)
			if hwnd is None:
				continue
			embed_window(hwnd, int(host_widget.winId()))
			self.embedded_hwnds[key] = hwnd

		self._resize_embedded_windows()

	def _resize_embedded_windows(self) -> None:
		if len(self.embedded_hwnds) < 1:
			return

		calib_geom = self.calib_host.contentsRect()
		serial_geom = self.serial_host.contentsRect()

		if "calibration" in self.embedded_hwnds:
			resize_window(self.embedded_hwnds["calibration"], 0, 0, calib_geom.width(), calib_geom.height())
		if "serial" in self.embedded_hwnds:
			resize_window(self.embedded_hwnds["serial"], 0, 0, serial_geom.width(), serial_geom.height())

	def resizeEvent(self, event):
		super().resizeEvent(event)
		QtCore.QTimer.singleShot(0, self._resize_embedded_windows)

	def poll_child_processes(self) -> None:
		if self.stop_event.is_set():
			return

		for name, process in self.processes.items():
			if process.poll() is not None:
				self.request_stop(f"{name} plot exited")
				return

	def closeEvent(self, event):
		self.request_stop("Demo closed")
		super().closeEvent(event)


def run_hand_motion(duration_s: float, stop_event: threading.Event, hand) -> None:

	def sleep_with_stop(seconds: float) -> bool:
		deadline = time.time() + seconds
		while time.time() < deadline:
			if stop_event.is_set():
				return False
			time.sleep(min(0.05, deadline - time.time()))
		return True

	def enable_torque_with_bounded_retry(max_attempts: int = 1) -> bool:
		# In the hardware-less demo we don't attempt low-level torque writes.
		# If a real hand is passed that supports `motor_ids` and a client,
		# this simple implementation will still let the demo proceed.
		try:
			if getattr(hand, "motor_ids", None) is None:
				return True
			return True
		except Exception:
			return False

	def disconnect_hand_safely() -> None:
		try:
			hand.disconnect()
		except Exception as exc:
			print(f"Warning: hand disconnect failed: {exc}")

	try:
		if not enable_torque_with_bounded_retry():
			stop_event.set()
			return

		# Avoid set_control_mode here: OrcaHand implementation can block indefinitely
		# when torque writes fail on faulty hardware.
		hand.set_max_current(hand.max_current)
		hand._compute_wrap_offsets_dict()

		current_pose = hand.get_joint_pos(as_list=False)
		base_pose = build_pointing_pose(hand)
		lift_pose = build_lift_pose(base_pose)
		press_pose = build_press_pose(base_pose)

		if current_pose and all(value is not None for value in current_pose.values()):
			start_pose = dict(current_pose)
		else:
			start_pose = dict(lift_pose)

		smooth_move(hand, start_pose, num_steps=BOOT_LIFT_STEPS, step_size=BOOT_LIFT_STEP_SIZE)
		if not sleep_with_stop(0.25):
			return
		smooth_move(hand, lift_pose, num_steps=BOOT_LIFT_STEPS, step_size=BOOT_LIFT_STEP_SIZE)
		if not sleep_with_stop(0.25):
			return

		demo_end = time.time() + duration_s
		cycle = 0
		while time.time() < demo_end and not stop_event.is_set():
			cycle += 1
			print(f"Demo cycle {cycle}")

			for t in range(HOLD_STEPS + 1):
				if time.time() >= demo_end or stop_event.is_set():
					break
				phase = press_path(t / HOLD_STEPS)
				intermediate_pose = dict(lift_pose)
				intermediate_pose["index_mcp"] = lift_pose["index_mcp"] + (press_pose["index_mcp"] - lift_pose["index_mcp"]) * phase
				intermediate_pose["index_pip"] = lift_pose["index_pip"] + (press_pose["index_pip"] - lift_pose["index_pip"]) * phase
				smooth_move(hand, intermediate_pose, num_steps=1, step_size=0.0)
				if not sleep_with_stop(HOLD_STEP_SIZE):
					break

			for t in range(HOLD_STEPS + 1):
				if time.time() >= demo_end or stop_event.is_set():
					break
				phase = press_path(t / HOLD_STEPS)
				intermediate_pose = dict(press_pose)
				intermediate_pose["index_mcp"] = press_pose["index_mcp"] + (lift_pose["index_mcp"] - press_pose["index_mcp"]) * phase
				intermediate_pose["index_pip"] = press_pose["index_pip"] + (lift_pose["index_pip"] - press_pose["index_pip"]) * phase
				smooth_move(hand, intermediate_pose, num_steps=1, step_size=0.0)
				if not sleep_with_stop(HOLD_STEP_SIZE):
					break

		smooth_move(hand, lift_pose, num_steps=BOOT_LIFT_STEPS, step_size=BOOT_LIFT_STEP_SIZE)
	finally:
		disconnect_hand_safely()
		stop_event.set()


def list_serial_ports() -> None:
	ports = list(list_ports.comports())
	if not ports:
		print("No serial ports detected.")
		return
	print("Available serial ports:")
	for p in ports:
		print(f" - {p.device}: hwid={p.hwid or ''} serial={getattr(p, 'serial_number', None)} description={p.description}")


def auto_detect_ports(preferred_hand_serial: str = "FTAK89GBA") -> tuple[str | None, str | None]:
	ports = list(list_ports.comports())
	if not ports:
		return None, None

	hand_candidates = []
	serial_candidates = []
	for p in ports:
		hwid = (p.hwid or "").lower()
		desc = (p.description or "").lower()
		sn = getattr(p, "serial_number", None)

		if sn == preferred_hand_serial or "0403:6014" in hwid:
			hand_candidates.append(p.device)
			continue

		# Heuristics for RP2040 / microcontroller used by the serial plotter
		if "rp2040" in desc or "rp2" in desc or "raspberry" in desc or "pio" in desc or "pico" in desc:
			serial_candidates.append(p.device)
			continue

		# Fallback: add to serial candidates if not a hand candidate
		serial_candidates.append(p.device)

	hand_port = hand_candidates[0] if hand_candidates else None
	serial_port = None
	# Prefer COM4 if present for serial plotter, else first non-hand port
	devices = [p.device for p in ports]
	if "COM4" in devices:
		serial_port = "COM4"
	elif serial_candidates:
		serial_port = serial_candidates[0]
	else:
		# Choose a port that isn't the hand, if possible
		for d in devices:
			if d != hand_port:
				serial_port = d
				break

	return hand_port, serial_port


def connect_hand_with_retry(hand_port: str | None = None):
	# If the real OrcaHand is available and a hardware port is requested,
	# try to use it. Otherwise fall back to the FakeOrcaHand.
	if REAL_ORCA_AVAILABLE:
		# If a specific port was given, try that first.
		if hand_port:
			hand = OrcaHand(str(MODEL_PATH))
			hand.port = hand_port
			print(f"Using provided hand port: {hand.port}")
			last_message = ""
			for attempt in range(1, HAND_CONNECT_RETRIES + 1):
				connected, message = hand.connect()
				last_message = message
				print(message)
				if connected:
					return hand
				if attempt < HAND_CONNECT_RETRIES:
					time.sleep(HAND_CONNECT_RETRY_DELAY_S)
			print(f"Failed to connect to Orca hand on {hand.port}: {last_message}")
		else:
			# Try auto-detection
			try:
				detected = resolve_hand_port()
				hand = OrcaHand(str(MODEL_PATH))
				hand.port = detected
				print(f"Auto-detected hand port: {hand.port}")
				last_message = ""
				for attempt in range(1, HAND_CONNECT_RETRIES + 1):
					connected, message = hand.connect()
					last_message = message
					print(message)
					if connected:
						return hand
					if "Access is denied" not in message:
						break
					if attempt < HAND_CONNECT_RETRIES:
						time.sleep(HAND_CONNECT_RETRY_DELAY_S)
				print(f"Could not connect to Orca hand on {hand.port}: {last_message}")
			except Exception as exc:
				print(f"Hand auto-detect failed: {exc}")

	# Real hand not available or connection failed — show ports and use fake hand.
	list_serial_ports()
	print("Falling back to FakeOrcaHand for demo (no hardware control).")
	hand = FakeOrcaHand(str(MODEL_PATH))
	hand.port = hand_port or "(fake)"
	connected, message = hand.connect()
	print(message)
	if connected:
		return hand
	raise RuntimeError("Failed to create fake Orca hand")


def main() -> int:
	args = parse_args()

	# Auto-detect ports when the user didn't provide them on the CLI.
	detected_hand, detected_serial = auto_detect_ports()
	if args.hand_port is None:
		args.hand_port = detected_hand or "COM5"
	if args.serial_port is None:
		args.serial_port = detected_serial or "COM4"

	print(f"Using hand port: {args.hand_port}")
	print(f"Using serial plotter port: {args.serial_port}")

	stop_event = threading.Event()
	mutex_handle = acquire_single_instance_mutex()
	hand = connect_hand_with_retry(args.hand_port)

	calibration_process = launch_process([sys.executable, str(CALIB_SCRIPT), args.calibration_command])
	serial_process = launch_process([sys.executable, str(SERIAL_SCRIPT), "--port", args.serial_port, "--baud", str(args.serial_baud)])

	app = QtWidgets.QApplication(sys.argv)
	window = DemoWindow(stop_event)
	window.register_processes(calibration_process, serial_process)
	window.show()

	hand_thread = threading.Thread(target=run_hand_motion, args=(args.duration, stop_event, hand), daemon=True)
	hand_thread.start()

	try:
		exit_code = app.exec()
	finally:
		stop_event.set()
		hand_thread.join(timeout=5.0)

		window.close_embedded_windows()

		deadline = time.time() + 5.0
		for process in (serial_process, calibration_process):
			while process.poll() is None and time.time() < deadline:
				time.sleep(0.1)

		window.terminate_child_processes()

		deadline = time.time() + 5.0
		for process in (serial_process, calibration_process):
			while process.poll() is None and time.time() < deadline:
				time.sleep(0.1)
			if process.poll() is None:
				process.kill()

		kernel32.CloseHandle(mutex_handle)

	return exit_code


if __name__ == "__main__":
	raise SystemExit(main())