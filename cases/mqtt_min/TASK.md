# MQTT minimum broker

Build an independent multi-file MQTT 3.1.1 broker in C99 for Linux TCP.
Use the accompanying REQUIREMENTS.md to select the protocol subset, and
the supplied MQTT standard PDF as the only source of protocol rules.
Deliver src/, include/, Makefile, README.md and the executable mqtt_broker.
Start it as ./mqtt_broker <port>. It must build using GCC and make.

Prefer a single-threaded poll() event loop and in-memory connection and
subscription state. These are engineering defaults, not protocol facts.
Public types, interfaces, ownership and processing paths must be planned
before implementing the project. Private helpers may be chosen during coding.

This task does not request a full MQTT implementation. Do not import an
existing broker or depend on Mosquitto as the implementation.

