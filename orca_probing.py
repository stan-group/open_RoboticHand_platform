from __future__ import annotations

import threading
import queue
from threads.logger_thread import logger_thread
import argparse
import csv
from pathlib import Path
import time

import orca_hand as oh

from sensors import factory as sf

##This code will run a probing experiment using the Orca Hand V1
## The desired finger is selected by the user (index, middle, ring, or pinky) default is index
## The offset CSV path is given by the user, default is probing_offsets.csv in the Probing Experiment folder

## First the hand will be booted into using functions from orca_hand.py
## The initial angles of the hand will be found using the function from orca_hand.py and stored in zero_pos HandPose class
## The lift_pos is the same class, created from the zero_pos plus lift offset
## The hand is started with the hand in the zero_pos, then moved to the lift_pos
## For each of the offsets in the CSV the hand moves from the lift_pos to the zero_pos + offset
## After all offsets are done the hand goes back to the zero_pos and powers down


PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_PATH = PROJECT_ROOT / "models" / "orcahand_v1_right"
DEFAULT_OFFSETS_PATH = PROJECT_ROOT / "Probing Experiment" / "probing_offsets.csv"
DEFAULT_FINGER = "index"
DEFAULT_STEP_COUNT = 80
DEFAULT_STEP_DELAY_S = 0.01


FINGER_JOINTS = {
	"index": ("index_abd", "index_mcp", "index_pip"),
	"middle": ("middle_abd", "middle_mcp", "middle_pip"),
	"ring": ("ring_abd", "ring_mcp", "ring_pip"),
	"pinky": ("pinky_abd", "pinky_mcp", "pinky_pip"),
}


LIFT_DELTA = {
	"wrist": -4,
	"abd":   0,
	"mcp":   0,
	"pip":   -7
}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run a probing sequence on the Orca hand.")
	parser.add_argument("--finger", choices=tuple(FINGER_JOINTS), default=DEFAULT_FINGER, help="Finger to probe")
	parser.add_argument("--offsets-file", type=Path, default=DEFAULT_OFFSETS_PATH, help="CSV file containing probing offsets")
	parser.add_argument("--model-path", type=Path, default=MODEL_PATH, help="Orca hand model directory")
	parser.add_argument("--hand-port", default=None, help="Serial port for the Orca hand")
	parser.add_argument("--finger_sensor_type", type="", default = "TLE493D_W2B6", help="The sensor type on the finger, make sure to use the sensor is in sensors/factory.py")
	parser.add_argument("--load_sensor_type", type="", default = "nidaqmx", help="The load sensor type on the setup, make sure to use the sensor is in sensors/factory.py")
	return parser.parse_args()


def load_probe_offsets(path: Path):
    offsets = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # convert numeric fields if present
            for key, value in row.items():
                try:
                    row[key] = float(value)
                except ValueError:
                    pass
            offsets.append(row)
    return offsets

def run_probing_experiment() -> int:
	args = parse_args()
	offsets = load_probe_offsets(args.offsets_file)
	hand = None
	finger = args.finger

	sensor = sf.create_sensor(sensor_type="TLE493D_W2B6")
	loadcell = sf.create_sensor(sensor_type="nidaqmx")

	log_queue = queue.Queue()
	current_joint_pos = {}
	threading.Thread(target=logger_thread, args=(log_queue,),  kwargs={"sensor_name": args.finger_sensor_type}, daemon=True,).start()

	try:
		hand = oh.connect_hand(model_path = args.model_path)
		hand.enable_torque()
		hand.set_max_current(hand.max_current)
		hand._compute_wrap_offsets_dict()

		zero_pose = oh.get_current_pose(hand)
		lift_pose = oh.HandPose(**zero_pose.offset(finger, wrist=LIFT_DELTA["wrist"], abd=LIFT_DELTA["abd"], mcp=LIFT_DELTA["mcp"], pip=LIFT_DELTA["pip"]))

		print("Moving to lift pose...")
		hand.set_joint_pos(lift_pose.dict(), num_steps=DEFAULT_STEP_COUNT, step_size=DEFAULT_STEP_DELAY_S)
		time.sleep(1)
		print("Probing {} points".format(len(offsets)))
		count = 1
		for offset in offsets:
			print("Probing point {}".format(count))
			count += 1
			probe_pose = oh.HandPose(**zero_pose.offset(finger, wrist=offset["wrist"], abd=offset["abd"], mcp=offset["mcp"], pip=offset["pip"]))
			hand.set_joint_pos(probe_pose.dict(), num_steps=DEFAULT_STEP_COUNT, step_size=DEFAULT_STEP_DELAY_S)
			time.sleep(0.5)
			hand.set_joint_pos(lift_pose.dict(), num_steps=DEFAULT_STEP_COUNT, step_size=DEFAULT_STEP_DELAY_S)
			time.sleep(0.5)

		print("Returning to zero pose...")
		hand.set_joint_pos(zero_pose.dict(), num_steps=DEFAULT_STEP_COUNT, step_size=DEFAULT_STEP_DELAY_S)
		print("Probing sequence finished.")
		return 0
	finally:
		if hand is not None:
			try:
				hand.disconnect()
			except:
				raise RuntimeError("Failed to disconnect from Orca_Hand")


def main() -> int:
	return run_probing_experiment()


if __name__ == "__main__":
	raise SystemExit(main())

