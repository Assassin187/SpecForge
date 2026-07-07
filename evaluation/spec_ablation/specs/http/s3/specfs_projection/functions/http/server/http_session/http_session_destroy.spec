[PROMPT]
Implement function `http_session_destroy`. Responsibility: 销毁底层连接对象并释放会话；空指针安全

[RELY]
- FUNC `http_connection_destroy`
  role: 销毁底层连接对象
```c
void http_connection_destroy(http_connection_t* conn);
```

[GUARANTEE]
```c
void http_session_destroy(http_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 session。

**Post-Condition**:
- 无返回值；session 为 NULL 时无操作。

**Invariant**:
- session 为 NULL 时安全返回，不执行任何操作
- 必须先销毁 conn 再释放 session 自身
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- session 为有效的 http_session_t* 或 NULL；并发访问需由上层同步控制
- session 及其关联的连接对象被释放；session 为 NULL 时无操作

**System Algorithm**:
- 空安全检查→http_connection_destroy(session->conn)→free(session)。
