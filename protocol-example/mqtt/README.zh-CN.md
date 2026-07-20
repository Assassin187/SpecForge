# MQTT Broker Example

这是一个基于 TCP 和 epoll 的 MQTT 3.1.1 minimum broker C implementation，支持 `CONNECT`、QoS 0 `SUBSCRIBE`/`PUBLISH`、`PINGREQ`、`DISCONNECT` 和 `+`/`#` topic filters。

```bash
cd protocol-example/mqtt
make
./mqtt_broker 1884
```

默认端口为 `1884`。使用 `mosquitto-clients` 测试：

```bash
mosquitto_sub -h 127.0.0.1 -p 1884 -t 'demo/#' -q 0
mosquitto_pub -h 127.0.0.1 -p 1884 -t demo/topic -q 0 -m hello
```

使用 `make clean` 清理构建产物。QoS 1/2、authentication、Will messages、persistence 和完整 MQTT 3.1.1 coverage 不在当前范围内。
