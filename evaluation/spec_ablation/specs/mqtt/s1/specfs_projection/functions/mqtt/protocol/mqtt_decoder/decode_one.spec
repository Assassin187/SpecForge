[PROMPT]
Implement function `decode_one`. Responsibility: 按报文类型解码单个 MQTT 报文体并填充 mqtt_packet_t

[RELY]
- STRUCT `mqtt_packet_t`
  role: 解码输出报文结构体
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

- STRUCT `mqtt_subscribe_payload_t`
  role: SUBSCRIBE 载荷结构体
```c
typedef struct mqtt_subscribe_payload {
    uint16_t packet_id;
    mqtt_subscribe_topic_t* topics;
    size_t topic_count;
} mqtt_subscribe_payload_t;
```

- STRUCT `mqtt_publish_payload_t`
  role: PUBLISH 载荷结构体
```c
typedef struct mqtt_publish_payload {
    char* topic_name;
    uint8_t* payload;
    size_t payload_len;
    uint8_t qos;
    bool retain;
    bool dup;
    bool has_packet_id;
    uint16_t packet_id;
} mqtt_publish_payload_t;
```

- FUNC `read_string`
  role: 解析协议字符串/主题/过滤器
```c
static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out);
```

- FUNC `read_u16`
  role: 解析 keep_alive/packet_id 等字段
```c
static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out);
```

- FUNC `mqtt_packet_free`
  role: 错误分支释放已分配字段
```c
void mqtt_packet_free(mqtt_packet_t* p);
```

[GUARANTEE]
```c
static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为固定头首字节 header1、完整报文体 body/body_len 与输出 packet out。

**Post-Condition**:
- 成功返回 true 并填充 out；协议格式错误、未知类型或内存分配失败返回 false，已部分分配的临时资源会在失败路径释放。

**Invariant**:
- out 在解析前清零
- SUBSCRIBE/PUBLISH 的动态字段成功后转移给 out 并由 mqtt_packet_free 释放

**System Algorithm**:
- 依据 header1 报文类型分支解析 CONNECT/SUBSCRIBE/PUBLISH/PINGREQ/DISCONNECT，并填充 mqtt_packet_t。
