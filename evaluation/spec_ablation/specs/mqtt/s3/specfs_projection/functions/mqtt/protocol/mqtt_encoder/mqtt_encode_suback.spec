[PROMPT]
Implement function `mqtt_encode_suback`. Responsibility: 编码 MQTT SUBACK 响应报文

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
  role: 写入 packet_id
```c
static void put_u16(uint8_t* out, size_t* pos, uint16_t v);
```

[GUARANTEE]
```c
mqtt_bytes_t mqtt_encode_suback(uint16_t packet_id, const uint8_t* return_codes, size_t return_code_count);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 packet_id、返回码数组 return_codes 与数量 return_code_count

**Post-Condition**:
- 返回 SUBACK 字节流；内存分配失败返回空 bytes

**Invariant**:
- return_code_count 为 0 时允许无 payload
- 返回码字节顺序与输入数组一致

**System Algorithm**:
- 计算 body 与 Remaining Length，分配缓冲后写入固定头、packet_id 及返回码 payload（若有）
