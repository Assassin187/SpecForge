[PROMPT]
Implement function `mqtt_broker_run`. Responsibility: 驱动 Broker 主事件循环，持续处理网络层分发的连接与报文事件

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- FUNC `mqtt_tcp_server_run`
  role: 执行事件循环
```c
void mqtt_tcp_server_run(mqtt_tcp_server_t* s);
```

[GUARANTEE]
```c
void mqtt_broker_run(mqtt_broker_t* b);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 应用主流程在 broker 启动后主动调用以进入运行态
- Precondition: b 非 NULL 且 b->server 已由 mqtt_broker_create 初始化
- Input: 事件输入为 broker 实例 b；具体网络事件由 tcp_server 在循环内产生

**Post-Condition**:
- State Change: 会话集合、订阅路由与连接状态会随回调执行动态变化
- Response: 函数不直接返回业务结果；通过网络回包与内部状态更新体现处理效果

**Invariant**:
- None specified.

**System Algorithm**:
- 调用 mqtt_tcp_server_run，随后由 on_accept_cb/on_data_cb/on_close_cb 处理连接建立、数据到达与连接关闭
