[PROMPT]
Implement function `find_conn`. Responsibility: 按 fd 在连接链表中查找对应连接对象

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- STRUCT `struct conn_node`
  role: 连接链表节点
```c
struct conn_node;
```

- FUNC `mqtt_connection_fd`
  role: 比较连接 fd 完成查找
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

[GUARANTEE]
```c
static mqtt_connection_t* find_conn(mqtt_tcp_server_t* s, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 TCP server 对象 s 与待查找的 fd。

**Post-Condition**:
- 找到时返回链表中借用的 mqtt_connection_t*；未找到时返回 NULL。

**Invariant**:
- 不修改连接链表
- 返回指针仍由 server 连接链表拥有

**System Algorithm**:
- 遍历连接链表，按 mqtt_connection_fd 与目标 fd 比较并返回命中连接。
