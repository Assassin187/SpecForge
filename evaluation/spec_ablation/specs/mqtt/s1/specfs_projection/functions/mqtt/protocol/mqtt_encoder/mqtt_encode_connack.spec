[PROMPT]
Implement function `mqtt_encode_connack`. Responsibility: 编码 MQTT CONNACK 响应报文

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

[GUARANTEE]
```c
mqtt_bytes_t mqtt_encode_connack(bool session_present, uint8_t return_code);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 session_present 标志与 return_code

**Post-Condition**:
- 返回包含完整 CONNACK 字节流的 mqtt_bytes_t；分配失败时 len=0,data=NULL

**Invariant**:
- 输出格式符合 MQTT 3.1.1 CONNACK 结构
- 返回缓冲由调用方通过 mqtt_bytes_free 释放

**System Algorithm**:
- 计算可变头长度与 Remaining Length 编码长度；分配输出缓冲；写入固定头、Remaining Length、会话标志与返回码
