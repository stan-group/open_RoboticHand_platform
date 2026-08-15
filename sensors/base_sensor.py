"""
BaseSensor defines the standard interface that all sensors must follow in the
Orca hand experiment framework. The experiment code interacts only with this
interface, allowing different sensors to be swapped without changing any logic.

Each sensor module must define a class named `Sensor` that inherits from
BaseSensor and implements:

    connect()  - initialize hardware (serial, I2C, SPI, etc.)
    read()     - return one measurement as a dict (e.g. {"x": 1.2, "y": 0.5})
    report()   - describe the structure of the data returned by read()
    close()    - release hardware resources

The sensor factory (see sensors/factory.py) uses this interface to dynamically
load the correct sensor module based on a string identifier, e.g.:

    sensor = create_sensor("serial_xyz", port="/dev/ttyUSB0")

To add a new sensor:
    1. Create sensors/<name>_sensor.py
    2. Implement `class Sensor(BaseSensor): ...`
    3. Add the sensor name to SENSOR_MAP in factory.py

Example skeleton for a new sensor:
----------------------------------
    from sensors.base_sensor import BaseSensor

    class Sensor(BaseSensor):
        def __init__(self, port, baud=115200):
            self.port = port
            self.baud = baud

        def connect(self):
            # open serial port or initialize hardware
            pass

        def read(self):
            # return a dict containing sensor data
            return {"value": 0.0}

        def report(self):
            return "ExampleSensor: returns {'value': float}"

        def close(self):
            # release hardware resources
            pass
"""

class BaseSensor:
    """Abstract base class for all sensors used in the Orca hand experiments."""

    def connect(self):
        """Initialize hardware resources (serial port, I2C bus, SPI device, etc.)."""
        raise NotImplementedError("connect() must be implemented by the sensor class.")

    def read(self):
        """
        Return one complete sensor measurement as a dict.

        The dict structure must be consistent and documented in report().
        Example return value:
            {"x": 0.12, "y": -0.03, "z": 0.98}
        """
        raise NotImplementedError("read() must be implemented by the sensor class.")

    def report(self):
        """
        Return a human-readable description of the sensor and the structure of
        the data returned by read().

        Example:
            "SerialXYZSensor: returns {'x': float, 'y': float, 'z': float}"
        """
        raise NotImplementedError("report() must be implemented by the sensor class.")

    def close(self):
        """Release hardware resources and shut down the sensor safely."""
        raise NotImplementedError("close() must be implemented by the sensor class.")
