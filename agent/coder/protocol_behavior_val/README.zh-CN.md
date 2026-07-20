# Protocol Behavior Validation

本目录提供生成实现的运行时 smoke tests，通过真实 TCP 或 UDP 交互检查 minimum profile。

| Protocol | Slug | 主要检查 |
| --- | --- | --- |
| MQTT | `mqtt` | CONNECT、QoS 0 pub/sub、DISCONNECT、malformed input 和可选 Mosquitto interoperability |
| CoAP | `coap` | GET、error responses、options、malformed datagrams 和可选 libcoap interoperability |
| HTTP/1.1 | `http` | GET/HEAD、status codes、headers 和 connection close |
| SMTP | `smtp` | greeting、command sequencing、DATA、error responses 和 mail storage |

自包含场景只使用 Python standard library。可选 interoperability tools：

```bash
sudo apt install mosquitto-clients libcoap3-bin
```

## 使用

```bash
python3 -m agent coder test \
  --protocol mqtt \
  --project-dir /path/to/generated/mqtt \
  --binary mqtt_broker
```

`generate` 会在成功编译后运行同一组检查；`verify` 依次执行 structure validation、compilation 和 behavior validation。新增协议时，需要实现 `run(project_dir, binary_name, scenarios)` 并在 `__init__.py` 注册 slug。

这些场景不覆盖完整 RFC、全部错误路径或安全要求。
