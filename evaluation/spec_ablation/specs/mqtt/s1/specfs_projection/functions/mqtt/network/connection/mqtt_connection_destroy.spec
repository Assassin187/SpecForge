[PROMPT]
Implement function `mqtt_connection_destroy`. Responsibility: 销毁连接对象及其所有动态资源

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

- STRUCT `struct out_chunk`
  role: 输出队列块结构体
```c
struct out_chunk;
```

- FUNC `mqtt_connection_close`
  role: 销毁前先关闭 fd
```c
void mqtt_connection_close(mqtt_connection_t* c);
```

[GUARANTEE]
```c
void mqtt_connection_destroy(mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 连接移除或服务器停止时触发
- Precondition: c 可为空；非空时为合法连接对象
- Input: 输入为连接指针 c

**Post-Condition**:
- State Change: 连接对象及其缓存资源全部回收
- Response: 无返回值

**Invariant**:
- None specified.

**System Algorithm**:
- 先调用 mqtt_connection_close，再释放 in_data、输出链表各节点数据与 peer 字符串，最后释放对象
