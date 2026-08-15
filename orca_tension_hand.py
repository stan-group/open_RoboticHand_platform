from pathlib import Path

from orca_hand import tension_hand


def main() -> int:
	model_path = Path(__file__).resolve().parent / "models" / "orcahand_v1_right"
	return tension_hand(model_path)


if __name__ == "__main__":
	raise SystemExit(main())