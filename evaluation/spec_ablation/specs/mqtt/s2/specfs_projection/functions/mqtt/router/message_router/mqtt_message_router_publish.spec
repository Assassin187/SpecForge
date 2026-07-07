[PROMPT]
Implement function `mqtt_message_router_publish`. Responsibility: 将发布消息转发给匹配订阅者会话

[RELY]
- STRUCT `struct mqtt_message_router`
  role: 消息路由器私有状态结构体
```c
struct mqtt_message_router;
```

- STRUCT `mqtt_publish_payload_t`
  role: 发布报文载荷结构体
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

- FUNC `mqtt_topic_tree_match_subscribers`
  role: 匹配目标订阅会话
```c
bool mqtt_topic_tree_match_subscribers(const mqtt_topic_tree_t* t, const char* topic, int** out_sids, size_t* out_count);
```

- FUNC `mqtt_session_manager_get`
  role: 按 sid 获取会话
```c
mqtt_session_t* mqtt_session_manager_get(mqtt_session_manager_t* m, int session_id);
```

- FUNC `mqtt_encode_publish_qos0`
  role: 编码下发 PUBLISH
```c
mqtt_bytes_t mqtt_encode_publish_qos0(const char* topic_name, const uint8_t* payload, size_t payload_len, bool retain);
```

- FUNC `mqtt_session_send`
  role: 向命中会话发送消息
```c
void mqtt_session_send(mqtt_session_t* s, const uint8_t* data, size_t len);
```

- FUNC `mqtt_bytes_free`
  role: 释放编码缓冲
```c
void mqtt_bytes_free(mqtt_bytes_t* b);
```

[GUARANTEE]
```c
void mqtt_message_router_publish(mqtt_message_router_t* r, int from_session_id, const mqtt_publish_payload_t* publish);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为路由器 r、发布源 from_session_id、发布载荷 publish

**Post-Condition**:
- 无返回值；匹配会话收到转发消息

**Invariant**:
- 每个订阅者独立编码并发送，单个失败不阻断其他会话
- subs 动态数组在函数末尾必须释放

**System Algorithm**:
- 忽略 from_session_id；校验 r/publish/topic_name；调用 topic_tree_match_subscribers 获取订阅者；逐个查 session、编码 QoS0 PUBLISH 并发送；最后释放 subs 数组
