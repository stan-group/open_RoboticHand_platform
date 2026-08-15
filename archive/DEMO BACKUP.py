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
from orca_core import OrcaHand
from orca_core.hardware.dynamixel_client import ADDR_TORQUE_ENABLE

from BACKUP.Orca_v1_DEMO import (
	BOOT_LIFT_STEP_SIZE,
	BOOT_LIFT_STEPS,
	HOLD_STEP_SIZE,
	HOLD_STEPS,
	MODEL_PATH,
	build_lift_pose,
	build_pointing_pose,
	build_press_pose,
	press_path,
	resolve_hand_port,
	smooth_move,
)


PROJECT_ROOT = Path(__file__).resolve().parent
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
	parser.add_argument("--serial-port", default="COM4", help="Port for Serial_3D.py (default: COM4)")
	parser.add_argument("--serial-baud", type=int, default=115200, help="Baud rate for Serial_3D.py (default: 115200)")
	parser.add_argument("--calibration-command", default="visualise", choices=["visualise", "visualise_gimbal"], help="Calibration visualisation mode to launch")
	parser.add_argument("--wait-for-windows", type=float, default=15.0, help="Seconds to wait for plot windows before embedding them")
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


def run_hand_motion(duration_s: float, stop_event: threading.Event, hand: OrcaHand) -> None:

	def sleep_with_stop(seconds: float) -> bool:
		deadline = time.time() + seconds
		while time.time() < deadline:
			if stop_event.is_set():
				return False
			time.sleep(min(0.05, deadline - time.time()))
		return True

	def enable_torque_with_bounded_retry(max_attempts: int = 3) -> bool:
		remaining_ids = list(hand.motor_ids)
		for attempt in range(1, max_attempts + 1):
			with hand._motor_lock:
				remaining_ids = hand._dxl_client.write_byte(remaining_ids, 1, ADDR_TORQUE_ENABLE)
			if not remaining_ids:
				return True
			if attempt < max_attempts:
				time.sleep(0.1)
		print(f"Failed to enable torque for motor IDs: {remaining_ids}")
		return False

	def disconnect_hand_safely() -> None:
		dxl_client = hand._dxl_client
		if dxl_client is None:
			return
		try:
			with hand._motor_lock:
				dxl_client.disconnect()
		except Exception as exc:
			print(f"Warning: hand disconnect failed: {exc}")
		finally:
			hand._dxl_client = None

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


def connect_hand_with_retry() -> OrcaHand:
	hand = OrcaHand(str(MODEL_PATH))
	hand.port = resolve_hand_port()
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

	raise RuntimeError(f"Could not connect to the Orca hand on {hand.port}: {last_message}")


def main() -> int:
	args = parse_args()
	stop_event = threading.Event()
	mutex_handle = acquire_single_instance_mutex()
	hand = connect_hand_with_retry()

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