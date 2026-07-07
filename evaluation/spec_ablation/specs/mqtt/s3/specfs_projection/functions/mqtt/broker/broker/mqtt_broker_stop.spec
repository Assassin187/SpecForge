[PROMPT]
Implement function `mqtt_broker_stop`. Responsibility: 请求停止 Broker 底层 TCP 服务与事件循环

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- FUNC `mqtt_tcp_server_stop`
  role: 停止 TCP 服务器并回收连接
```c
void mqtt_tcp_server_stop(mqtt_tcp_server_t* s);
```

[GUARANTEE]
```c
void mqtt_broker_stop(mqtt_broker_t* b);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 broker 指针 b（可为 NULL）

**Post-Condition**:
- 无返回值；有效输入下会让 run 循环在底层可停止点退出

**Invariant**:
- 停止操作不释放 Broker 自身内存
- 重复调用在空对象或已停止状态下保持安全

**System Algorithm**:
- 当 b 或 b->server 无效时直接返回；否则调用 mqtt_tcp_server_stop 发出停止请求
