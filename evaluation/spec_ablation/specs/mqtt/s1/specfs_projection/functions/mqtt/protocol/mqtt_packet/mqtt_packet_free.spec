[PROMPT]
Implement function `mqtt_packet_free`. Responsibility: 按报文类型释放 mqtt_packet_t 内部动态字段

[RELY]
- STRUCT `mqtt_packet_t`
  role: MQTT 报文结构体
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

[GUARANTEE]
```c
void mqtt_packet_free(mqtt_packet_t* p);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为报文对象指针 p（可为空）

**Post-Condition**:
- 无返回值；调用后报文字段不再持有动态资源

**Invariant**:
- 空指针安全返回
- 仅释放当前 type 对应字段，避免越界释放

**System Algorithm**:
- 按 type 分支释放 CONNECT.client_id、PUBLISH.topic_name/payload、SUBSCRIBE.topic 列表；清空相关长度/指针并将 type 置为 RESERVED
