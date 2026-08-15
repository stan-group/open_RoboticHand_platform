#include <Wire.h>
#include "TLx493D_inc.hpp"

using namespace ifx::tlx493d;

// Use A0 variant unless your chip is A1/A2/A3
TLx493D_W2B6 dut(Wire, TLx493D_IIC_ADDR_A0_e);

void setup() {
    Serial.begin(9600);
    delay(1500);
    Serial.println("Starting TLE493D-W2B6 on RP2040 Zero...");

    // Configure I2C pins for Waveshare RP2040 Zero
    Wire.setSDA(4);   // GP4
    Wire.setSCL(5);   // GP5
    Wire.begin();
    Wire.setClock(1000000);

    // Initialize sensor
    dut.begin();

    Serial.println("Sensor initialized.\n");
}

void loop() {
    double x, y, z, t;

    // Recommended default sensitivity
    dut.setSensitivity(TLx493D_FULL_RANGE_e);

    if (dut.getMagneticFieldAndTemperature(&x, &y, &z, &t)) {
        Serial.print("X:");
        Serial.print(x);
        Serial.print(" Y:");
        Serial.print(y);
        Serial.print(" Z:");
        Serial.print(z);
        Serial.print(" T:");
        Serial.println(t);
    } else {
        Serial.println("Read error (I2C wiring or address)");
    }

    delay(100);
}
