# Protocol Behavior Validation

This directory contains runtime smoke tests for generated implementations. The tests use real TCP or UDP exchanges to check each minimum profile.

| Protocol | Slug | Main checks |
| --- | --- | --- |
| MQTT | `mqtt` | CONNECT, QoS 0 pub/sub, DISCONNECT, malformed input, and optional Mosquitto interoperability |
| CoAP | `coap` | GET, error responses, options, malformed datagrams, and optional libcoap interoperability |
| HTTP/1.1 | `http` | GET/HEAD, status codes, headers, and connection close |
| SMTP | `smtp` | greeting, command sequencing, DATA, error responses, and mail storage |

All self-contained scenarios use only the Python standard library. Optional interoperability tools:

```bash
sudo apt install mosquitto-clients libcoap3-bin
```

## Usage

```bash
python3 -m agent coder test \
  --protocol mqtt \
  --project-dir /path/to/generated/mqtt \
  --binary mqtt_broker
```

`generate` runs the same checks after a successful compile. `verify` runs structure validation, compilation, and behavior validation in sequence. To add a protocol, implement `run(project_dir, binary_name, scenarios)` and register its slug in `__init__.py`.

These scenarios do not cover a complete RFC, every error path, or security requirements.
