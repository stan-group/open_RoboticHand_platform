from __future__ import annotations

from pathlib import Path
import time
import math

from orca_core import OrcaHand
from serial.tools import list_ports


MODEL_PATH = Path(__file__).resolve().parent / "models" / "orcahand_v1_right"
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


def build_pointing_pose(hand: OrcaHand) -> dict[str, float]:
    return {
        "thumb_mcp": 50,   # Thumb MCP: flexion/extension of the thumb base (positive closes thumb across palm)
        "thumb_abd": 42,   # Thumb ABD: abduction/adduction (moves thumb away/toward the palm plane)
        "thumb_pip": 108,  # Thumb PIP: proximal interphalangeal-like bend (thumb middle joint)
        "thumb_dip": 112,  # Thumb DIP: distal thumb bend (thumb tip joint)
        "index_abd": 28,    # Index ABD: index finger spread (positive moves index away from middle finger)
        "index_mcp": 0,    # Index MCP: main knuckle flexion/extension (positive closes the finger)
        "index_pip": -20,  # Index PIP: middle joint flexion (more positive => more bend)
        "middle_abd": 37,  # Middle ABD: spread of middle finger (usually small, keeps fingers aligned)
        "middle_mcp": 91,  # Middle MCP: middle finger knuckle flexion
        "middle_pip": 107, # Middle PIP: middle finger second-joint flexion
        "ring_abd": 37,    # Ring ABD: spread of ring finger
        "ring_mcp": 91,    # Ring MCP: ring finger knuckle flexion
        "ring_pip": 107,   # Ring PIP: ring finger second-joint flexion
        "pinky_abd": 37,   # Pinky ABD: spread of the little finger
        "pinky_mcp": 98,   # Pinky MCP: little finger knuckle flexion
        "pinky_pip": 108,  # Pinky PIP: little finger middle-joint flexion
        "wrist": -25,      # Wrist: wrist flexion/extension (sign/direction follow the model config)
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


def smooth_move(hand: OrcaHand, target_pose: dict[str, float], *, num_steps: int, step_size: float) -> None:
    hand.set_joint_pos(target_pose, num_steps=num_steps, step_size=step_size)


def press_path(progress: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * progress)


def main() -> int:
    hand = OrcaHand(str(MODEL_PATH))
    # Use a fixed port for the Orca hand to avoid incorrect auto-detection.
    hand.port = "COM5"
    print(f"Using hand port: {hand.port} (hardcoded)")

    connected, message = hand.connect()
    print(message)
    if not connected:
        return 1

    try:
        hand.enable_torque()
        hand.set_control_mode(hand.control_mode)
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

        # Lift the index first so the hand starts from a non-contact pose.
        smooth_move(hand, start_pose, num_steps=BOOT_LIFT_STEPS, step_size=BOOT_LIFT_STEP_SIZE)
        time.sleep(0.3)

        smooth_move(hand, lift_pose, num_steps=BOOT_LIFT_STEPS, step_size=BOOT_LIFT_STEP_SIZE)
        time.sleep(0.2)

        for cycle in range(PRESS_CYCLES):
            print(f"Press cycle {cycle + 1}/{PRESS_CYCLES}")
            for t in range(HOLD_STEPS + 1):
                phase = press_path(t / HOLD_STEPS)
                intermediate_pose = dict(lift_pose)
                intermediate_pose["index_mcp"] = lift_pose["index_mcp"] + (press_pose["index_mcp"] - lift_pose["index_mcp"]) * phase
                intermediate_pose["index_pip"] = lift_pose["index_pip"] + (press_pose["index_pip"] - lift_pose["index_pip"]) * phase
                smooth_move(hand, intermediate_pose, num_steps=1, step_size=0.0)
                time.sleep(HOLD_STEP_SIZE)

            time.sleep(0.1)

            for t in range(HOLD_STEPS + 1):
                phase = press_path(t / HOLD_STEPS)
                intermediate_pose = dict(press_pose)
                intermediate_pose["index_mcp"] = press_pose["index_mcp"] + (lift_pose["index_mcp"] - press_pose["index_mcp"]) * phase
                intermediate_pose["index_pip"] = press_pose["index_pip"] + (lift_pose["index_pip"] - press_pose["index_pip"]) * phase
                smooth_move(hand, intermediate_pose, num_steps=1, step_size=0.0)
                time.sleep(HOLD_STEP_SIZE)

            time.sleep(0.1)

        return 0
    finally:
        hand.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())