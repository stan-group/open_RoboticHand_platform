from __future__ import annotations

from pathlib import Path
import re
import time
import msvcrt

from orca_core import OrcaHand
from serial.tools import list_ports
from orca_hand_pose import HandPose

def resolve_hand_port_interactive() -> str:
    """Ask the user to select a serial port, showing detailed device info."""
    ports = list(list_ports.comports())
    print("\nMultiple serial devices detected. Select the Orca hand:\n")

    for i, p in enumerate(ports):
        print(f"[{i}] {p.device}")
        print(f"     Description : {p.description}")
        print(f"     Manufacturer: {p.manufacturer}")
        print(f"     Serial No.  : {getattr(p, 'serial_number', None)}")
        if p.vid and p.pid:
            print(f"     VID:PID     : {p.vid}:{p.pid}")
        else:
            print("     VID:PID     : N/A")
        print(f"     HWID        : {p.hwid}")
        print()

    while True:
        choice = input("Enter number: ").strip()
        if choice.isdigit() and int(choice) < len(ports):
            return ports[int(choice)].device
        print("Invalid selection. Try again.")


def resolve_hand_port() -> str:
    """Robust autoconnect: try VID/PID first, then description, then interactive."""
    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("No serial ports detected.")

    # 1. Match FTDI VID:PID (most reliable)
    ftdi_matches = [
        p for p in ports
        if "VID:PID=0403:6014" in (p.hwid or "")
    ]
    if len(ftdi_matches) == 1:
        return ftdi_matches[0].device

    # 2. Match description (fallback)
    desc_matches = [
        p for p in ports
        if "FTDI" in (p.description or "") or "USB Serial" in (p.description or "")
    ]
    if len(desc_matches) == 1:
        return desc_matches[0].device

    # 3. If multiple FTDI matches, pick the newest (highest COM number)
    if len(ftdi_matches) > 1:
        sorted_ports = sorted(
            ftdi_matches,
            key=lambda p: int(re.sub(r"\D", "", p.device))
        )
        return sorted_ports[-1].device

    # 4. If only one port exists, use it
    if len(ports) == 1:
        return ports[0].device

    # 5. Ambiguous → ask the user
    print("Could not auto-detect the Orca hand port automatically.")
    return resolve_hand_port_interactive()


def connect_hand(model_path: Path, hand_port: str | None = None) -> OrcaHand:
    # 1. Resolve port automatically
    if hand_port is None:
        hand_port = resolve_hand_port()  # your new combined auto+interactive version

    # 2. Create hand object
    hand = OrcaHand(str(model_path))
    hand.port = hand_port
    print(f"Using hand port: {hand.port}")

    # 3. Attempt connection
    time.sleep(0.2)
    connected, message = hand.connect()
    print(message)

    if not connected:
        raise RuntimeError(
            f"Failed to connect to Orca hand on {hand_port}. "
            f"Message: {message}"
        )

    return hand


def get_current_joint_angles(hand: OrcaHand, *, as_list: bool = False):
	"""Return the current joint angles reported by the hand wrapper.
	"""
	getter = getattr(hand, "get_joint_pos", None)
	if getter is None:
		raise AttributeError("The supplied hand object does not expose get_joint_pos().")
	return getter(as_list=as_list)


def get_current_pose(hand) -> HandPose:
	angles = get_current_joint_angles(hand, as_list=False)
	if isinstance(angles, dict) and angles:
		return HandPose(**angles)
	raise RuntimeError("Failed to read the current hand joint angles.")


def calibrate_hand(model_path: Path, hand_port: str | None = None) -> int:
	hand = OrcaHand(str(model_path))
	hand.port = resolve_hand_port() if hand_port is None else hand_port
	print(f"Auto-detected hand port: {hand.port}")

	connected, message = hand.connect()
	print(message)
	if not connected:
		return 1

	try:
		hand.calibrate()
		print("Calibration finished.")
		print(f"Calibrated: {hand.is_calibrated()}")
		return 0
	finally:
		hand.disconnect()


def build_tension_pose() -> dict[str, float]:
	return {
		"thumb_mcp": 18,
		"thumb_abd": 28,
		"thumb_pip": 18,
		"thumb_dip": 50,
		"index_abd": 28,
		"index_mcp": 18,
		"index_pip": 12,
		"middle_abd": 37,
		"middle_mcp": 22,
		"middle_pip": 18,
		"ring_abd": 37,
		"ring_mcp": 22,
		"ring_pip": 18,
		"pinky_abd": 37,
		"pinky_mcp": 24,
		"pinky_pip": 20,
		"wrist": -25,
	}


def wait_for_escape() -> None:
	print("Holding tension. Press Esc to release and exit.")
	while True:
		if msvcrt.kbhit():
			key = msvcrt.getwch()
			if key == "\x1b":
				return
		time.sleep(0.05)


def tension_hand(model_path: Path, hand_port: str | None = None) -> int:
	"""
	Move the hand into a predefined tension pose and enable torque so the user
	can tighten the mechanical ratchets. This does NOT calibrate the hand.
	"""
	hand = OrcaHand(str(model_path))
	hand.port = resolve_hand_port() if hand_port is None else hand_port
	print(f"Auto-detected hand port: {hand.port}")

	connected, message = hand.connect()
	print(message)
	if not connected:
		return 1

	try:
		hand.enable_torque()
		hand.set_max_current(hand.max_current)

		tension_pose = build_tension_pose()
		hand.set_joint_pos(tension_pose, num_steps=60, step_size=0.01)
		print("Tension pose applied.")
		wait_for_escape()
		return 0
	finally:
		hand.disconnect()

	
