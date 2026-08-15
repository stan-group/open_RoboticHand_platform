from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from itertools import product
from pathlib import Path

# The following command can be copied to generate new probing_offsets.csv
# python "Probing Experiment/generate_offsets.py" --wrist-min 0 --wrist-max 0 --wrist-resolution 1 --abd-min -5 --abd-max 5 --abd-resolution 11 --mcp-min 0 --mcp-max 5 --mcp-resolution 5 --pip-min 0 --pip-max 5 --pip-resolution 5
# To enable overwriting of an existing file add the argument: --overwrite
# The path of the created csv file is:
# "Probing Experiment/probing_offsets.py"

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "probing_offsets.csv"
JOINT_NAMES = ("wrist", "abd", "mcp", "pip")


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Generate randomized probing offsets for grid search.")
	parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="CSV file to write (default: probing_offsets.csv in this folder)")
	parser.add_argument("--overwrite", action="store_true", help="Overwrite the output file without prompting")

	for joint_name in JOINT_NAMES:
		parser.add_argument(f"--{joint_name}-min", type=float, required=True, help=f"Minimum {joint_name} offset")
		parser.add_argument(f"--{joint_name}-max", type=float, required=True, help=f"Maximum {joint_name} offset")
		parser.add_argument(f"--{joint_name}-resolution", type=int, required=True, help=f"Grid resolution for {joint_name}")

	return parser.parse_args()


def build_axis_values(minimum: float, maximum: float, resolution: int) -> list[float]:
    values: list[float] = []
    if resolution <= 0:
        raise ValueError("Resolution must be greater than zero.")
    if maximum < minimum:
        raise ValueError("Maximum must be greater than or equal to minimum.")
    if maximum == minimum or resolution == 1:
        values.append(round(minimum, 3))
    else: 
        step = (maximum - minimum)/resolution
        for i in range(resolution -1):
            values.append(round(minimum + i * step, 3))
    return values


def build_grid_rows(args: argparse.Namespace) -> list[dict[str, float]]:
	axis_values = {
		joint_name: build_axis_values(
			getattr(args, f"{joint_name}_min"),
			getattr(args, f"{joint_name}_max"),
			getattr(args, f"{joint_name}_resolution"),
		)
		for joint_name in JOINT_NAMES
	}

	rows = [
		{
			"wrist": wrist,
			"abd": abd,
			"mcp": mcp,
			"pip": pip,
		}
		for wrist, abd, mcp, pip in product(
			axis_values["wrist"],
			axis_values["abd"],
			axis_values["mcp"],
			axis_values["pip"],
		)
	]
	random.shuffle(rows)
	return rows


def prompt_before_overwrite(output_path: Path) -> None:
	message = (
		f"{output_path} already exists. "
		"Press Enter to overwrite it, or Esc to quit: "
	)

	if sys.platform == "win32":
		import msvcrt

		print(message, end="", flush=True)
		while True:
			key = msvcrt.getwch()
			if key == "\r":
				print()
				return
			if key == "\x1b":
				print("\nCancelled.")
				raise SystemExit(1)

	response = input(message)
	if response.strip():
		print("Cancelled.")
		raise SystemExit(1)


def write_offsets_csv(output_path: Path, rows: list[dict[str, float]], overwrite: bool) -> None:
	if output_path.exists() and not overwrite:
		prompt_before_overwrite(output_path)

	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open("w", encoding="utf-8", newline="") as handle:
		writer = csv.DictWriter(handle, fieldnames=["wrist", "abd", "mcp", "pip"])
		writer.writeheader()
		writer.writerows(rows)


def main() -> int:
	args = parse_args()
	rows = build_grid_rows(args)
	write_offsets_csv(args.output, rows, args.overwrite)

	print(f"Wrote {len(rows)} randomized offset rows to {args.output}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

