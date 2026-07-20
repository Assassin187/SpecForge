# MQTT Broker Example

This MQTT 3.1.1 minimum broker in C uses TCP and epoll. It supports `CONNECT`, QoS 0 `SUBSCRIBE`/`PUBLISH`, `PINGREQ`, `DISCONNECT`, and `+`/`#` topic filters.

```bash
cd protocol-example/mqtt
make
./mqtt_broker 1884
```

The default port is `1884`. Test with `mosquitto-clients`:

```bash
mosquitto_sub -h 127.0.0.1 -p 1884 -t 'demo/#' -q 0
mosquitto_pub -h 127.0.0.1 -p 1884 -t demo/topic -q 0 -m hello
```

Run `make clean` to remove build artifacts. QoS 1/2, authentication, Will messages, persistence, and complete MQTT 3.1.1 coverage are out of scope.
