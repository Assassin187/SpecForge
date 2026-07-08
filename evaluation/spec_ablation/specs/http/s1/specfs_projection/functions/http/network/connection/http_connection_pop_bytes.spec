[PROMPT]
Implement function `http_connection_pop_bytes`. Responsibility: 从输入缓冲提取指定长度的字节，动态分配返回；数据不足返回 NULL

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

[GUARANTEE]
```c
char* http_connection_pop_bytes(http_connection_t* conn, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(http_connection_t*，不可为 NULL，BORROWED)；len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回动态分配的字节缓冲区（调用方负责 free）；数据不足返回 NULL。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 conn->in_buf：从中复制 len 字节
- 维护 conn->in_len：校验是否 >= len，提取后缩减 len

**System Algorithm**:
- 校验 in_len >= len 且 len > 0；malloc 分配 len+1 字节并 memcpy；memmove 消费缓冲中 len 字节。
