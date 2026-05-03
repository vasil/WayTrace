# WayTrace

Part of the **Open Streets Initiative** — accessibility and pavement quality
mapping through sensor data collection.

## What it does
Records accelerometer and gyroscope at 10 Hz from an Android phone mounted
on a wheelchair or walking device. Data is saved as CSV to the phone's
Downloads folder for later analysis and OpenStreetMap contribution.

## CSV output format
timestamp_ms, sensor, x, y, z

- timestamp_ms: milliseconds since device boot
- sensor: accel or gyro
- x, y, z: axis values (m/s² for accel, rad/s for gyro)

## Roadmap
- v1.1: Synchronized video recording
- v1.2: GPS track overlay

## License
Open source — part of Open Streets Initiative
