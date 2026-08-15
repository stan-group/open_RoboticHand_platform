from __future__ import annotations

import archive.nidaqmx as nidaqmx
from nidaqmx.system import System
from nidaqmx.constants import TerminalConfiguration
from typing import Dict, Optional

from sensors.base_sensor import BaseSensor

class Sensor(BaseSensor):
    """
    Methods:
        connect()  - auto-detect NI device or ask user
        read()     - read ai0, ai1, ai2
        report()   - return last sample
        close()    - close task
    """

    def __init__(self, device_name: Optional[str] = None) -> None:
        self.device_name = device_name
        self.task: Optional[nidaqmx.Task] = None
        self.last_sample: Optional[Dict[str, float]] = None

    # ----------------------------------------------------------------------
    # DEVICE RESOLUTION
    # ----------------------------------------------------------------------

    def _auto_detect_device(self) -> str:
        """Return the first NI device, or ask user if multiple exist."""
        system = System.local()
        devices = list(system.devices)

        if not devices:
            raise RuntimeError("No NI‑DAQmx devices detected.")

        if self.device_name:
            # User explicitly provided a device name
            for dev in devices:
                if dev.name == self.device_name:
                    return dev.name
            raise RuntimeError(f"Requested device '{self.device_name}' not found.")

        if len(devices) == 1:
            return devices[0].name

        # Multiple devices → interactive picker
        print("\nMultiple NI devices detected. Select one:\n")
        for i, dev in enumerate(devices):
            print(f"[{i}] {dev.name} — {dev.product_type}")

        while True:
            choice = input("Enter number: ").strip()
            if choice.isdigit() and int(choice) < len(devices):
                return devices[int(choice)].name
            print("Invalid selection. Try again.")

    # ----------------------------------------------------------------------
    # CONNECTION
    # ----------------------------------------------------------------------

    def connect(self) -> None:
        """Create a task and configure ai0, ai1, ai2."""
        device = self._auto_detect_device()

        self.task = nidaqmx.Task()

        # Add channels ai0, ai1, ai2
        for chan in ("ai0", "ai1", "ai2"):
            physical = f"{device}/{chan}"
            self.task.ai_channels.add_ai_voltage_chan(
                physical,
                min_val=-10.0,
                max_val=10.0,
                terminal_config=TerminalConfiguration.RSE,
            )

        print(f"Connected to NI device: {device}")

    # ----------------------------------------------------------------------
    # READ
    # ----------------------------------------------------------------------

    def read(self) -> Dict[str, float]:
        """Read ai0, ai1, ai2 and return them as a dict."""
        if self.task is None:
            raise RuntimeError("Sensor not connected.")

        values = self.task.read(timeout=5.0)

        # NI returns a list of floats when multiple channels are read
        sample = {
            "ai0": float(values[0]),
            "ai1": float(values[1]),
            "ai2": float(values[2]),
        }

        self.last_sample = sample
        return sample

    # ----------------------------------------------------------------------
    # REPORT
    # ----------------------------------------------------------------------

    def report(self) -> Dict[str, float]:
        """Return the last read sample."""
        if self.last_sample is None:
            raise RuntimeError("No sample has been read yet.")
        return dict(self.last_sample)

    # ----------------------------------------------------------------------
    # CLOSE
    # ----------------------------------------------------------------------

    def close(self) -> None:
        """Close the NI task."""
        if self.task is not None:
            self.task.close()
            self.task = None
            print("NI‑DAQ sensor connection closed.")
