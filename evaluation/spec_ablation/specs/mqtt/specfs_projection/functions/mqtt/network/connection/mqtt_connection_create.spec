[PROMPT]
Implement function `mqtt_connection_create`. Responsibility: 创建连接对象并初始化输入输出缓冲状态

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
mqtt_connection_t* mqtt_connection_create(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: accept 新连接并获得 client fd 后触发
- Precondition: fd 应为可用 socket 描述符
- Input: 输入为连接 fd

**Post-Condition**:
- State Change: 产生新的连接实例，进入可读写初始状态
- Response: 成功返回连接指针，失败返回 NULL

**Invariant**:
- None specified.

**System Algorithm**:
- 分配连接对象并设置 fd、closed=false、输入缓冲空、输出队列空、peer 为空
