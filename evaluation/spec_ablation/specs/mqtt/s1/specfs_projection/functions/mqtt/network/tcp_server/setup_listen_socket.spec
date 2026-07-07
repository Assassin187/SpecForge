[PROMPT]
Implement function `setup_listen_socket`. Responsibility: 创建并配置监听 socket（SO_REUSEADDR、非阻塞、bind、listen）

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- FUNC `set_nonblocking`
  role: 设置监听 socket 为非阻塞
```c
static bool set_nonblocking(int fd);
```

[GUARANTEE]
```c
static bool setup_listen_socket(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为已分配并设置 port 的 TCP server 对象 s。

**Post-Condition**:
- 成功返回 true，并创建非阻塞 listen_fd、设置 SO_REUSEADDR、完成 bind/listen；任一步失败返回 false。

**Invariant**:
- 成功路径留下可由 stop 关闭的 listen_fd
- 失败路径不启动 running 状态

**System Algorithm**:
- 创建监听 socket，设置 SO_REUSEADDR 和非阻塞，然后 bind+listen。
