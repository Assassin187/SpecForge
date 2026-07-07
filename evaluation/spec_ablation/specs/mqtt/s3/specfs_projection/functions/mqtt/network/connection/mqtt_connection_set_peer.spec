[PROMPT]
Implement function `mqtt_connection_set_peer`. Responsibility: 更新并保存连接对端地址字符串副本

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
void mqtt_connection_set_peer(mqtt_connection_t* c, const char* peer);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: accept 新连接后设置对端 ip:port 时触发
- Precondition: c 应有效；peer 可为空
- Input: 输入为连接 c 与 peer 字符串

**Post-Condition**:
- State Change: 连接的 peer 标识被覆盖更新
- Response: 无返回值

**Invariant**:
- None specified.

**System Algorithm**:
- 释放旧 peer；peer 非空则 strdup 保存新值，空值则保持 NULL
