[PROMPT]
Implement function `mqtt_encode_publish_qos0`. Responsibility: 编码 MQTT PUBLISH(QoS0) 报文用于消息转发

[RELY]
- STRUCT `mqtt_bytes_t`
  role: 编码字节缓冲结构体
```c
typedef struct mqtt_bytes {
    uint8_t* data;
    size_t len;
} mqtt_bytes_t;
```

- FUNC `remaining_length_bytes`
  role: 计算剩余长度编码字节数
```c
static size_t remaining_length_bytes(size_t len);
```

- FUNC `make_bytes`
  role: 分配输出缓冲
```c
static mqtt_bytes_t make_bytes(size_t len);
```

- FUNC `put_remaining_length`
  role: 写入 Remaining Length
```c
static void put_remaining_length(uint8_t* out, size_t* pos, size_t len);
```

- FUNC `put_u16`
  role: 写入 topic 长度
```c
static void put_u16(uint8_t* out, size_t* pos, uint16_t v);
```

[GUARANTEE]
```c
mqtt_bytes_t mqtt_encode_publish_qos0(const char* topic_name, const uint8_t* payload, size_t payload_len, bool retain);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 topic_name、payload、payload_len 与 retain 标志

**Post-Condition**:
- 返回 PUBLISH 字节流；参数或分配不合法时返回空 bytes

**Invariant**:
- QoS 固定为 0，不写 packet_id
- retain 位仅由 retain 参数决定

**System Algorithm**:
- 校验 topic_name 非空且长度<=65535；计算可变头与总长度；分配缓冲；写入固定头、Remaining Length、topic 长度与字符串、payload
