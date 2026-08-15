from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

import orca_hand as oh


PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_PATH = PROJECT_ROOT / "models" / "orcahand_v1_right"

JOINT_GROUPS: dict[str, tuple[str, ...]] = {
	"Thumb": ("thumb_mcp", "thumb_abd", "thumb_pip", "thumb_dip"),
	"Index": ("index_abd", "index_mcp", "index_pip"),
	"Middle": ("middle_abd", "middle_mcp", "middle_pip"),
	"Ring": ("ring_abd", "ring_mcp", "ring_pip"),
	"Pinky": ("pinky_abd", "pinky_mcp", "pinky_pip"),
	"Wrist": ("wrist",),
}


@dataclass(frozen=True)
class JointLimit:
	minimum: float
	maximum: float


DEFAULT_LIMITS: dict[str, JointLimit] = {
	"thumb_mcp": JointLimit(-50, 50),
	"thumb_abd": JointLimit(-20, 42),
	"thumb_pip": JointLimit(-12, 108),
	"thumb_dip": JointLimit(-20, 112),
	"index_abd": JointLimit(-37, 37),
	"index_mcp": JointLimit(-20, 95),
	"index_pip": JointLimit(-20, 108),
	"middle_abd": JointLimit(-37, 37),
	"middle_mcp": JointLimit(-20, 91),
	"middle_pip": JointLimit(-20, 107),
	"ring_abd": JointLimit(-37, 37),
	"ring_mcp": JointLimit(-20, 91),
	"ring_pip": JointLimit(-20, 107),
	"pinky_abd": JointLimit(-37, 37),
	"pinky_mcp": JointLimit(-20, 98),
	"pinky_pip": JointLimit(-20, 108),
	"wrist": JointLimit(-50, 30),
}


def load_joint_limits() -> dict[str, JointLimit]:
	config_limits = dict(DEFAULT_LIMITS)
	config_path = MODEL_PATH / "config.yaml"
	if not config_path.exists():
		return config_limits

	try:
		import yaml
	except Exception:
		return config_limits

	data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
	joint_roms = data.get("joint_roms", {}) if isinstance(data, dict) else {}
	if not isinstance(joint_roms, dict):
		return config_limits

	for joint_name, bounds in joint_roms.items():
		if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
			continue
		try:
			config_limits[joint_name] = JointLimit(float(bounds[0]), float(bounds[1]))
		except (TypeError, ValueError):
			continue

	return config_limits


class JointControl(QtWidgets.QWidget):
	valueChanged = QtCore.pyqtSignal(str, float)

	def __init__(self, joint_name: str, limits: JointLimit, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)
		self.joint_name = joint_name
		self.limits = limits

		layout = QtWidgets.QHBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(6)

		self.label = QtWidgets.QLabel(joint_name.replace("_", " ").title(), self)
		self.label.setMinimumWidth(115)
		layout.addWidget(self.label)

		self.minus_button = QtWidgets.QToolButton(self)
		self.minus_button.setText("-")
		self.minus_button.setAutoRepeat(True)
		self.minus_button.clicked.connect(self.decrement)
		layout.addWidget(self.minus_button)

		self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
		self.slider.setRange(0, 1000)
		self.slider.valueChanged.connect(self._slider_changed)
		layout.addWidget(self.slider, 1)

		self.plus_button = QtWidgets.QToolButton(self)
		self.plus_button.setText("+")
		self.plus_button.setAutoRepeat(True)
		self.plus_button.clicked.connect(self.increment)
		layout.addWidget(self.plus_button)

		self.value_box = QtWidgets.QDoubleSpinBox(self)
		self.value_box.setDecimals(2)
		self.value_box.setRange(limits.minimum, limits.maximum)
		self.value_box.setSingleStep(max((limits.maximum - limits.minimum) / 200.0, 0.1))
		self.value_box.valueChanged.connect(self._spin_changed)
		layout.addWidget(self.value_box)

		self._updating = False
		self.set_value(limits.minimum)

	def _value_to_slider(self, value: float) -> int:
		span = self.limits.maximum - self.limits.minimum
		if span <= 0:
			return 0
		return int(round((value - self.limits.minimum) / span * 1000))

	def _slider_to_value(self, slider_value: int) -> float:
		span = self.limits.maximum - self.limits.minimum
		if span <= 0:
			return self.limits.minimum
		return self.limits.minimum + (slider_value / 1000.0) * span

	def _slider_changed(self, slider_value: int) -> None:
		if self._updating:
			return
		self._updating = True
		try:
			self.value_box.blockSignals(True)
			self.value_box.setValue(self._slider_to_value(slider_value))
		finally:
			self.value_box.blockSignals(False)
			self._updating = False
		self.valueChanged.emit(self.joint_name, self.value())

	def _spin_changed(self, value: float) -> None:
		if self._updating:
			return
		self._updating = True
		try:
			self.slider.blockSignals(True)
			self.slider.setValue(self._value_to_slider(value))
		finally:
			self.slider.blockSignals(False)
			self._updating = False
		self.valueChanged.emit(self.joint_name, self.value())

	def set_value(self, value: float) -> None:
		clamped = min(max(value, self.limits.minimum), self.limits.maximum)
		self._updating = True
		try:
			self.slider.blockSignals(True)
			self.value_box.blockSignals(True)
			self.slider.setValue(self._value_to_slider(clamped))
			self.value_box.setValue(clamped)
		finally:
			self.slider.blockSignals(False)
			self.value_box.blockSignals(False)
			self._updating = False

	def value(self) -> float:
		return float(self.value_box.value())

	def increment(self) -> None:
		self.set_value(self.value() + self.value_box.singleStep())

	def decrement(self) -> None:
		self.set_value(self.value() - self.value_box.singleStep())


class HandControlWindow(QtWidgets.QMainWindow):
	def __init__(self) -> None:
		super().__init__()
		self.setWindowTitle("Orca Hand GUI")
		self.resize(1280, 900)

		self.hand = None
		self._updating_from_hand = False
		self._boot_complete = False
		self.joint_controls: dict[str, JointControl] = {}
		self.joint_limits = load_joint_limits()

		central = QtWidgets.QWidget(self)
		self.setCentralWidget(central)
		root_layout = QtWidgets.QVBoxLayout(central)
		root_layout.setContentsMargins(12, 12, 12, 12)
		root_layout.setSpacing(10)

		self.status_label = QtWidgets.QLabel("Connecting to hand...", self)
		self.status_label.setWordWrap(True)
		root_layout.addWidget(self.status_label)

		self.scroll_area = QtWidgets.QScrollArea(self)
		self.scroll_area.setWidgetResizable(True)
		root_layout.addWidget(self.scroll_area, 1)

		container = QtWidgets.QWidget(self.scroll_area)
		self.scroll_area.setWidget(container)
		self.container_layout = QtWidgets.QVBoxLayout(container)
		self.container_layout.setContentsMargins(6, 6, 6, 6)
		self.container_layout.setSpacing(10)

		self._build_controls()

		self.footer = QtWidgets.QLabel("Booting hand and reading live position...", self)
		root_layout.addWidget(self.footer)

		self.refresh_button = QtWidgets.QPushButton("Read Current Position", self)
		self.refresh_button.clicked.connect(self.sync_from_hand)
		root_layout.addWidget(self.refresh_button)

		QtCore.QTimer.singleShot(0, self.boot_hand)

	def _build_controls(self) -> None:
		for group_name, joint_names in JOINT_GROUPS.items():
			box = QtWidgets.QGroupBox(group_name, self)
			box_layout = QtWidgets.QVBoxLayout(box)
			box_layout.setSpacing(8)

			for joint_name in joint_names:
				limits = self.joint_limits.get(joint_name, DEFAULT_LIMITS[joint_name])
				control = JointControl(joint_name, limits, self)
				control.valueChanged.connect(self._joint_value_changed)
				self.joint_controls[joint_name] = control
				box_layout.addWidget(control)

			self.container_layout.addWidget(box)

		self.container_layout.addStretch(1)

	def boot_hand(self) -> None:
		try:
			self.hand = oh.connect_hand_with_retry(MODEL_PATH)
			self.hand.enable_torque()
			self.hand.set_max_current(self.hand.max_current)
			self.hand._compute_wrap_offsets_dict()
			self.sync_from_hand()
			current_pose = self.current_pose()
			self.hand.set_joint_pos(current_pose, num_steps=1, step_size=0.0)
			self.status_label.setText("Connected. Sliders now mirror the hand's current position.")
			self._boot_complete = True
		except Exception as exc:
			self.status_label.setText(f"Failed to connect to hand: {exc}")
			self.refresh_button.setEnabled(False)

	def sync_from_hand(self) -> None:
		if self.hand is None:
			return
		try:
			pose = oh.get_current_joint_angles(self.hand, as_list=False)
			self._updating_from_hand = True
			for joint_name, control in self.joint_controls.items():
				if joint_name in pose:
					control.set_value(float(pose[joint_name]))
			self.footer.setText("Current joint position synced from hand.")
		except Exception as exc:
			self.footer.setText(f"Could not read current position: {exc}")
		finally:
			self._updating_from_hand = False

	def _joint_value_changed(self, joint_name: str, value: float) -> None:
		if self._updating_from_hand or self.hand is None:
			return
		pose = self.current_pose()
		try:
			self.hand.set_joint_pos(pose, num_steps=1, step_size=0.0)
			self.footer.setText(f"Moved {joint_name} to {value:.2f}")
		except Exception as exc:
			self.footer.setText(f"Failed to move {joint_name}: {exc}")

	def current_pose(self) -> dict[str, float]:
		return {joint_name: control.value() for joint_name, control in self.joint_controls.items()}

	def closeEvent(self, event) -> None:
		if self.hand is not None:
			try:
				self.hand.disconnect()
			except Exception:
				pass
		super().closeEvent(event)


def main() -> int:
	app = QtWidgets.QApplication(sys.argv)
	window = HandControlWindow()
	window.show()
	return app.exec_()


if __name__ == "__main__":
	raise SystemExit(main())