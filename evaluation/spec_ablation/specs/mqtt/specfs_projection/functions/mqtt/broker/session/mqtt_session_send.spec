[PROMPT]
Implement function `mqtt_session_send`. Responsibility: 通过会话对应连接发送 MQTT 编码后的字节流

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

- FUNC `mqtt_connection_send`
  role: 通过底层连接发送字节流
```c
void mqtt_connection_send(mqtt_connection_t* c, const uint8_t* data, size_t len);
```

[GUARANTEE]
```c
void mqtt_session_send(mqtt_session_t* s, const uint8_t* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: broker 需要下发 CONNACK/SUBACK/PINGRESP 等响应包时触发
- Precondition: s 与 s->conn 非空，data 非空且 len>0
- Input: 输入为会话、待发送缓冲区 data 及长度 len

**Post-Condition**:
- State Change: 会话结构体本身不变，连接发送缓冲可能被更新
- Response: 无返回值；发送结果由底层连接层负责处理

**Invariant**:
- None specified.

**System Algorithm**:
- 参数非法则直接返回；参数有效时调用 mqtt_connection_send(s->conn, data, len)
