[PROMPT]
Implement function `remove_conn`. Responsibility: 按 fd 从链表中摘除并销毁连接节点

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
  role: 匹配目标连接 fd
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

- FUNC `mqtt_connection_destroy`
  role: 销毁移除的连接对象
```c
void mqtt_connection_destroy(mqtt_connection_t* c);
```

[GUARANTEE]
```c
static void remove_conn(mqtt_tcp_server_t* s, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 TCP server 对象 s 与待移除的 fd。

**Post-Condition**:
- 若 fd 命中连接节点，则从链表摘除该节点、销毁连接并释放节点；未命中时不改变链表。

**Invariant**:
- 移除后链表不再包含该 fd
- 只销毁被摘除的连接节点

**System Algorithm**:
- 遍历链表找到目标 fd 后摘链，销毁连接对象并释放节点内存。
