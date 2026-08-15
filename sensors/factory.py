"""
Sensor Factory

This module provides a single function, `create_sensor()`, which instantiates
the correct sensor class based on a string identifier. It allows the experiment
code to remain completely independent of specific sensor implementations.

How to add a new sensor module:
--------------------------------
1. Create a new file inside sensors/, e.g. `imu_sensor.py`.
2. Implement a `Sensor` class inside that file, inheriting from BaseSensor.
3. Ensure the module name and class follow this pattern:
       sensors/<name>_sensor.py  →  class Sensor(BaseSensor)
4. Add an entry to the SENSOR_MAP dictionary below:
       "imu": "imu_sensor"
5. Now the experiment script can select it via:
       sensor = create_sensor("imu", port="/dev/ttyUSB0")

The experiment code never needs to import individual sensor modules directly.
It only calls create_sensor(), keeping the system modular and easy to extend.
"""

from sensors.base_sensor import BaseSensor

# Map sensor type names to module filenames (without .py)
SENSOR_MAP = {
    "TLE493D_W2B6": "TLE493D_W2B6_sensor",
    "nidaqmx": "nidaqmx_sensor",
    # Add new sensors here
}


def create_sensor(sensor_type: str, **kwargs) -> BaseSensor:
    """
    Create and return an instance of a sensor class based on the sensor_type.

    Parameters
    ----------
    sensor_type : str
        A key from SENSOR_MAP, e.g. "TLE493D_W2B6", "nidaqmx".
    **kwargs :
        Arguments passed directly to the sensor's constructor.

    Returns
    -------
    BaseSensor
        An instance of the selected sensor class.

    Raises
    ------
    ValueError
        If the sensor_type is unknown or the module does not contain a Sensor class.
    """

    if sensor_type not in SENSOR_MAP:
        raise ValueError(
            f"Unknown sensor type '{sensor_type}'. "
            f"Available types: {', '.join(SENSOR_MAP.keys())}"
        )

    module_name = SENSOR_MAP[sensor_type]

    try:
        module = __import__(f"sensors.{module_name}", fromlist=["Sensor"])
    except ImportError as e:
        raise ImportError(f"Failed to import sensor module '{module_name}': {e}")

    if not hasattr(module, "Sensor"):
        raise ValueError(
            f"Module '{module_name}' does not define a 'Sensor' class. "
            "Ensure the file contains: class Sensor(BaseSensor): ..."
        )

    SensorClass = module.Sensor
    return SensorClass(**kwargs)
