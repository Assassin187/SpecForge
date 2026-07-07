[PROMPT]
Implement function `on_data_cb`. Responsibility: 收包回调：定位会话、驱动解码并按已消费长度推进连接输入缓冲

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- STRUCT `struct packet_ctx`
  role: 解码回调上下文结构
```c
struct packet_ctx;
```

- FUNC `mqtt_connection_fd`
  role: 通过 fd 定位会话
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

- FUNC `mqtt_session_manager_get`
  role: 查询连接对应会话
```c
mqtt_session_t* mqtt_session_manager_get(mqtt_session_manager_t* m, int session_id);
```

- FUNC `mqtt_connection_in_data`
  role: 读取输入缓冲指针
```c
uint8_t* mqtt_connection_in_data(mqtt_connection_t* c);
```

- FUNC `mqtt_connection_in_len`
  role: 读取输入缓冲长度
```c
size_t mqtt_connection_in_len(const mqtt_connection_t* c);
```

- FUNC `mqtt_decoder_feed`
  role: 增量解码并触发 on_packet 回调
```c
bool mqtt_decoder_feed(const uint8_t* buffer, size_t buffer_len, size_t* out_consumed, mqtt_on_packet_fn on_packet, void* user);
```

- FUNC `mqtt_connection_in_consume`
  role: 消费已解析字节
```c
void mqtt_connection_in_consume(mqtt_connection_t* c, size_t n);
```

[GUARANTEE]
```c
static void on_data_cb(void* user, mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: tcp_server 在连接可读并完成 mqtt_connection_read 后触发
- Precondition: user 对应 broker，c 有效且已在 session_manager 中登记
- Input: 事件输入为 broker 上下文与有输入数据的连接 c

**Post-Condition**:
- State Change: 会话与路由状态可能因报文处理变化，连接输入缓冲会按已消费字节缩减
- Response: 无返回值；业务处理通过回调链路与状态更新体现

**Invariant**:
- None specified.

**System Algorithm**:
- 校验 b/c；通过 fd 获取会话 sess；读取连接 in_data/in_len，空数据直接返回；构造 packet_ctx 后调用 mqtt_decoder_feed 解析并回调 on_packet；若 consumed>0 则调用 mqtt_connection_in_consume 前移缓冲
