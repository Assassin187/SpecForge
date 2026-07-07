[PROMPT]
Implement function `mqtt_broker_start`. Responsibility: 启动底层 TCP 服务，开启 Broker 对客户端连接的监听能力

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- FUNC `mqtt_tcp_server_start`
  role: 启动 TCP 监听与 epoll
```c
bool mqtt_tcp_server_start(mqtt_tcp_server_t* s);
```

[GUARANTEE]
```c
bool mqtt_broker_start(mqtt_broker_t* b);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 broker 指针 b

**Post-Condition**:
- 返回 true 表示监听启动成功，false 表示参数无效或底层启动失败

**Invariant**:
- 仅在 server 已创建时才允许启动
- 函数本身不分配或释放 Broker 资源

**System Algorithm**:
- 校验 b 与 b->server 是否存在；若无效返回 false；有效时调用 mqtt_tcp_server_start 并返回其结果
