[PROMPT]
Implement function `mqtt_connection_close`. Responsibility: 关闭连接 fd 并标记连接为 closed

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
void mqtt_connection_close(mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 检测到协议错误、网络异常、对端断开或主动停止服务时触发
- Precondition: 允许 c 为空；允许重复关闭
- Input: 输入为连接 c

**Post-Condition**:
- State Change: 连接进入不可再读写的关闭状态
- Response: 无返回值

**Invariant**:
- None specified.

**System Algorithm**:
- 若 c 为空或已关闭则返回；否则置 closed=true，fd>=0 时执行 close 并置 fd=-1
