[PROMPT]
Implement function `handle_packet`. Responsibility: 按 MQTT 报文类型执行业务分发并驱动会话/路由/回包动作

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- STRUCT `mqtt_session_t`
  role: 当前连接对应会话对象
```c
typedef struct mqtt_session mqtt_session_t;
```

- STRUCT `mqtt_packet_t`
  role: 解码后的 MQTT 报文对象
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

- FUNC `mqtt_session_connection`
  role: 获取会话底层连接
```c
mqtt_connection_t* mqtt_session_connection(const mqtt_session_t* s);
```

- FUNC `mqtt_session_mark_connected`
  role: CONNECT 后标记会话已连接
```c
void mqtt_session_mark_connected(mqtt_session_t* s, const char* client_id, bool clean_session, uint16_t keep_alive);
```

- FUNC `mqtt_encode_connack`
  role: 编码 CONNACK 回包
```c
mqtt_bytes_t mqtt_encode_connack(bool session_present, uint8_t return_code);
```

- FUNC `mqtt_encode_suback`
  role: 编码 SUBACK 回包
```c
mqtt_bytes_t mqtt_encode_suback(uint16_t packet_id, const uint8_t* return_codes, size_t return_code_count);
```

- FUNC `mqtt_encode_pingresp`
  role: 编码 PINGRESP 回包
```c
mqtt_bytes_t mqtt_encode_pingresp(void);
```

- FUNC `mqtt_session_send`
  role: 向客户端发送编码后的报文
```c
void mqtt_session_send(mqtt_session_t* s, const uint8_t* data, size_t len);
```

- FUNC `mqtt_bytes_free`
  role: 释放编码缓冲
```c
void mqtt_bytes_free(mqtt_bytes_t* b);
```

- FUNC `mqtt_session_id`
  role: 读取会话 ID 用于路由/日志
```c
int mqtt_session_id(const mqtt_session_t* s);
```

- FUNC `mqtt_session_connected`
  role: 校验会话是否已 CONNECT
```c
bool mqtt_session_connected(const mqtt_session_t* s);
```

- FUNC `mqtt_connection_close`
  role: 协议不合法或断开时关闭连接
```c
void mqtt_connection_close(mqtt_connection_t* c);
```

- FUNC `mqtt_message_router_subscribe`
  role: 写入订阅关系
```c
void mqtt_message_router_subscribe(mqtt_message_router_t* r, int session_id, const char* filter);
```

- FUNC `mqtt_message_router_publish`
  role: 转发 PUBLISH 消息
```c
void mqtt_message_router_publish(mqtt_message_router_t* r, int from_session_id, const mqtt_publish_payload_t* publish);
```

[GUARANTEE]
```c
static void handle_packet(mqtt_broker_t* b, mqtt_session_t* sess, const mqtt_packet_t* pkt);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 broker 上下文 b、当前会话 sess 与已解码报文 pkt

**Post-Condition**:
- 无返回值；处理结果通过会话状态变更、路由更新与网络回包体现

**Invariant**:
- 未通过 CONNECT 的会话不得处理 SUBSCRIBE/PUBLISH/PINGREQ
- 编码函数返回的 mqtt_bytes_t 在使用后必须调用 mqtt_bytes_free 释放

**System Algorithm**:
- 先校验参数与会话连接；随后按报文类型分支：CONNECT 更新会话状态并回 CONNACK，SUBSCRIBE 在已连接前提下写入订阅并回 SUBACK，PUBLISH 在已连接且 QoS0 时路由转发，PINGREQ 回 PINGRESP，DISCONNECT 主动关闭连接；非法状态或资源失败时关闭连接或直接返回
