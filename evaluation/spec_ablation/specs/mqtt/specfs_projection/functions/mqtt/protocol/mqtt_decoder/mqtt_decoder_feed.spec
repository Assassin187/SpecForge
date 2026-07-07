[PROMPT]
Implement function `mqtt_decoder_feed`. Responsibility: 增量解码输入缓冲中的 MQTT 报文并回调上层处理

[RELY]
- STRUCT `struct remaining_length`
  role: Remaining Length 解析结果结构体
```c
struct remaining_length;
```

- STRUCT `mqtt_packet_t`
  role: 解码结果报文结构体
```c
typedef struct mqtt_packet {
    mqtt_packet_type_t type;
    union {
        mqtt_connect_payload_t connect;
        mqtt_publish_payload_t publish;
        mqtt_subscribe_payload_t subscribe;
    } v;
} mqtt_packet_t;
```

- FUNC `try_parse_remaining_length`
  role: 解析可变长剩余长度
```c
static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out);
```

- FUNC `decode_one`
  role: 解码单个完整报文
```c
static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out);
```

- FUNC `mqtt_packet_free`
  role: 释放解码分配的报文字段
```c
void mqtt_packet_free(mqtt_packet_t* p);
```

[GUARANTEE]
```c
bool mqtt_decoder_feed(const uint8_t* buffer, size_t buffer_len, size_t* out_consumed, mqtt_on_packet_fn on_packet, void* user);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为只读缓冲 buffer、缓冲长度 buffer_len、已消费长度输出指针 out_consumed、报文回调 on_packet 和用户上下文 user。

**Post-Condition**:
- 返回 true 表示解码过程完成（含仅部分数据未组成完整包），参数非法或遇到畸形 Remaining Length 时返回 false；*out_consumed 表示可由调用方从输入缓冲前缀消费的字节数

**Invariant**:
- 不会越界读取未到齐报文
- 匹配过程不修改输入缓冲内容
- 每个成功解码的 pkt 在回调后都执行 mqtt_packet_free

**System Algorithm**:
- 将 *out_consumed 初始化为 0；循环检查最小头部长度并解析 Remaining Length；仅在完整包到齐时调用 decode_one；解码成功则回调并释放 pkt；累计 consumed 后写入 *out_consumed，不修改输入缓冲。
