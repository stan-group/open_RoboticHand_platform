from pathlib import Path
import sys

from orca_hand import calibrate_hand


def main() -> int:
    model_path = Path(__file__).resolve().parent / "models" / "orcahand_v1_right"
    return calibrate_hand(model_path)


if __name__ == "__main__":
    raise SystemExit(main())