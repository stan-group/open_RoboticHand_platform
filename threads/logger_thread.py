import csv
import os
import time
from datetime import datetime
from queue import Queue


def _generate_default_path(sensor_name: str | None = None, extra: str | None = None) -> str:
    """
    Generate a timestamped CSV filename inside DATA/.

    Example:
        DATA/2026-07-31_15-03-12_serial_xyz_points50.csv
    """
    os.makedirs("DATA", exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    parts = [timestamp]

    if sensor_name:
        parts.append(sensor_name)

    if extra:
        parts.append(extra)

    filename = "_".join(parts) + ".csv"
    return os.path.join("DATA", filename)


def logger_thread(
    log_queue: Queue,
    path: str | None = None,
    *,
    sensor_name: str | None = None,
    extra: str | None = None,
):
    """
    Threaded CSV logger.

    - If `path` is None → create a timestamped file inside DATA/
    - Dynamically determines CSV headers from the first row
    - Writes rows as they arrive from the queue
    """

    # Determine output path
    if path is None:
        path = _generate_default_path(sensor_name=sensor_name, extra=extra)

    # Wait for the first row to determine headers
    first_row = log_queue.get()
    fieldnames = list(first_row.keys())

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(first_row)

        # Process remaining rows
        while True:
            row = log_queue.get()
            writer.writerow(row)
            log_queue.task_done()
