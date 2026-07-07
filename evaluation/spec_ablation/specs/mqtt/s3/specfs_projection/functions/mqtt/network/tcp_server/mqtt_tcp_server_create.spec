[PROMPT]
Implement function `mqtt_tcp_server_create`. Responsibility: 创建 TCP 服务器对象并初始化运行时字段

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

[GUARANTEE]
```c
mqtt_tcp_server_t* mqtt_tcp_server_create(uint16_t port, mqtt_tcp_callbacks_t cb, void* user);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为监听端口 port、回调集合 cb 与用户上下文 user

**Post-Condition**:
- 成功返回服务器对象，失败返回 NULL

**Invariant**:
- 新建对象在 start 前不持有任何系统 fd 资源
- 回调函数指针按入参原样保存供运行期调用

**System Algorithm**:
- 分配服务器对象并写入 port/cb/user，初始化 listen_fd=-1、epoll_fd=-1、running=false、conns=NULL
