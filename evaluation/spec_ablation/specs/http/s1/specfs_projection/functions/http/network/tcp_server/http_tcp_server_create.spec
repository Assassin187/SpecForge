[PROMPT]
Implement function `http_tcp_server_create`. Responsibility: calloc 分配 TCP server 对象，保存端口、回调、user 指针，初始化 listen_fd/epoll_fd 为 -1

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

[GUARANTEE]
```c
http_tcp_server_t* http_tcp_server_create(uint16_t port, http_tcp_callbacks_t callbacks, void* user);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入端口、回调结构体、user 指针。

**Post-Condition**:
- 成功返回 server 指针，失败返回 NULL。

**Invariant**:
- 新建对象在 start 前不持有任何系统 fd 资源
- 回调函数指针按入参原样保存供运行期调用
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- calloc 分配 server；设置 port/listen_fd=-1/epoll_fd=-1/callbacks/user/clients=NULL。
