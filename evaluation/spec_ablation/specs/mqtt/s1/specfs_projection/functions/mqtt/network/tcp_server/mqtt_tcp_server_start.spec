[PROMPT]
Implement function `mqtt_tcp_server_start`. Responsibility: 初始化监听与 epoll 资源并将服务器置为运行态

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- FUNC `setup_listen_socket`
  role: 创建监听 socket
```c
static bool setup_listen_socket(mqtt_tcp_server_t* s);
```

- FUNC `setup_epoll`
  role: 初始化 epoll 监听
```c
static bool setup_epoll(mqtt_tcp_server_t* s);
```

[GUARANTEE]
```c
bool mqtt_tcp_server_start(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为服务器指针 s

**Post-Condition**:
- 返回 true 表示启动成功，false 表示初始化失败

**Invariant**:
- 仅在监听 socket 与 epoll 都就绪后进入 running 状态
- 失败时不进入事件循环

**System Algorithm**:
- 校验 s 非空；依次调用 setup_listen_socket 与 setup_epoll；任一步失败返回 false；成功后置 running=true 并返回 true
