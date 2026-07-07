[PROMPT]
Implement function `http_connection_queue`. Responsibility: 将原始字节追加到输出缓冲队列；自动扩容

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

- FUNC `ensure_cap`
  role: 确保输出缓冲容量充足
```c
static int ensure_cap(uint8_t** buf, size_t* cap, size_t needed);
```

[GUARANTEE]
```c
int http_connection_queue(http_connection_t* conn, const void* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(http_connection_t*，不可为 NULL，BORROWED)；data(const void*，可为 NULL，BORROWED)；len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->out_buf：在 out_len 偏移处写入 len 字节
- 维护 conn->out_len：追加入队后递增 len
- 维护 conn->out_cap：容量不足时由 ensure_cap 更新

**System Algorithm**:
- 校验参数（data==NULL 且 len!=0 返回 -1；len==0 返回 0）；调用 ensure_cap 确保 out_buf 容量 >= out_len+len；memcpy 追加数据到 out_buf+out_len 并更新 out_len。
