[PROMPT]
Implement function `http_connection_has_pending`. Responsibility: 判断连接是否存在待发送数据（out_off < out_len）

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

[GUARANTEE]
```c
bool http_connection_has_pending(const http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(const http_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 通过返回值反映是否存在待发送数据。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->out_off：只读比较当前发送偏移
- 维护 conn->out_len：只读比较当前缓冲长度

**System Algorithm**:
- 空指针返回 false；否则返回 conn->out_off < conn->out_len。
