[PROMPT]
Implement function `mqtt_connection_want_write`. Responsibility: 判断连接是否存在待发送输出数据

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
bool mqtt_connection_want_write(const mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 事件循环决定 epoll 关注集与 flush 调度时触发
- Precondition: 允许 c 为空
- Input: 输入为连接 c

**Post-Condition**:
- State Change: 只读，无状态修改
- Response: 返回 true 表示有待写数据

**Invariant**:
- None specified.

**System Algorithm**:
- 判断 out_head 是否非空并返回结果
