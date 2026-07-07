[PROMPT]
Implement function `mqtt_encode_pingresp`. Responsibility: 编码 MQTT PINGRESP 报文

[RELY]
- STRUCT `mqtt_bytes_t`
  role: 编码字节缓冲结构体
```c
typedef struct mqtt_bytes {
    uint8_t* data;
    size_t len;
} mqtt_bytes_t;
```

- FUNC `make_bytes`
  role: 分配固定长度输出缓冲
```c
static mqtt_bytes_t make_bytes(size_t len);
```

[GUARANTEE]
```c
mqtt_bytes_t mqtt_encode_pingresp(void);
```

[SPECIFICATION]
**Pre-Condition**:
- 无输入参数

**Post-Condition**:
- 返回固定两字节 PINGRESP 缓冲；分配失败返回空 bytes

**Invariant**:
- 输出长度恒为 2
- 报文类型固定为 MQTT_PKT_PINGRESP

**System Algorithm**:
- 分配 2 字节缓冲并写入 PINGRESP 报文头与 Remaining Length=0
