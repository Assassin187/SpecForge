[PROMPT]
Implement function `on_packet`. Responsibility: 解码器回调入口：提取上下文并委托 handle_packet 执行实际报文处理

[RELY]
- STRUCT `struct packet_ctx`
  role: 解码回调上下文，包含 broker 与 session
```c
struct packet_ctx;
```

- FUNC `handle_packet`
  role: 委托执行具体报文处理逻辑
```c
static void handle_packet(mqtt_broker_t* b, mqtt_session_t* sess, const mqtt_packet_t* pkt);
```

[GUARANTEE]
```c
static void on_packet(void* user, const mqtt_packet_t* pkt);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: mqtt_decoder_feed 成功解出完整报文后触发回调
- Precondition: user 应指向包含 broker 与会话指针的 packet_ctx_t
- Input: 事件输入为 user 上下文与报文 pkt

**Post-Condition**:
- State Change: 本函数不直接改状态，状态变化由 handle_packet 完成
- Response: 无返回值；通过委托函数完成后续处理

**Invariant**:
- None specified.

**System Algorithm**:
- 将 user 转为 packet_ctx_t；校验 ctx、ctx->broker、ctx->sess 有效后调用 handle_packet(ctx->broker, ctx->sess, pkt)
