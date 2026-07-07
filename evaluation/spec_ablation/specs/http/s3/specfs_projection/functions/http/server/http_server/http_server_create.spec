[PROMPT]
Implement function `http_server_create`. Responsibility: 创建 HTTP server：realpath 解析 root→stat 验证目录→保存 root→创建 TCP server 并绑定回调

[RELY]
- STRUCT `struct http_server`
  role: http_server_create 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- FUNC `http_tcp_server_create`
  role: http_server_create 调用 http_tcp_server_create 完成子步骤
```c
http_tcp_server_t* http_tcp_server_create(uint16_t port, http_tcp_callbacks_t callbacks, void* user);
```

[GUARANTEE]
```c
http_server_t* http_server_create(uint16_t port, const char* root_dir);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入端口和 root 目录路径。

**Post-Condition**:
- 成功返回新分配并初始化的对象指针；分配或依赖初始化失败返回 NULL，并清理已分配资源。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- root_dir 非空且为有效路径字符串；port 为 1-65535 的有效端口号
- 成功时返回初始化的 http_server_t 指针；root 目录不存在或 realpath 失败时返回 NULL

**System Algorithm**:
- calloc 分配 server→realpath 规范化 root→stat 验证为目录→strncpy 保存 root→http_tcp_server_create 创建 TCP server 并绑定 on_accept/on_event/on_close 回调。任一失败清理返回 NULL。
