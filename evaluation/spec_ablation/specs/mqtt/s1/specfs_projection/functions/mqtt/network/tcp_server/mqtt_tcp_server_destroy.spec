[PROMPT]
Implement function `mqtt_tcp_server_destroy`. Responsibility: 销毁服务器对象并清理底层网络资源

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- FUNC `mqtt_tcp_server_stop`
  role: 销毁前先停止服务并清理连接
```c
void mqtt_tcp_server_stop(mqtt_tcp_server_t* s);
```

[GUARANTEE]
```c
void mqtt_tcp_server_destroy(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为服务器指针 s（可为空）

**Post-Condition**:
- 无返回值，调用后对象失效

**Invariant**:
- 销毁前统一经 stop 回收资源，避免 fd 泄漏
- NULL 输入不产生副作用

**System Algorithm**:
- s 为空直接返回；非空时先 mqtt_tcp_server_stop 回收连接与 fd，再 free(s)
