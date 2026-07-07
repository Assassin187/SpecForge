[PROMPT]
Implement function `http_connection_pop_line`. Responsibility: 从输入缓冲提取以 \n 分隔的一行，去除尾部 \r，动态分配返回；行不完整返回 NULL

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

[GUARANTEE]
```c
char* http_connection_pop_line(http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(http_connection_t*，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回动态分配的行字符串（调用方负责 free）；行不完整返回 NULL。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->in_buf：扫描 \n 并从中提取行数据
- 维护 conn->in_len：提取成功后通过 memmove 缩减

**System Algorithm**:
- 扫描 in_buf 查找 \n；未找到返回 NULL；找到后去除尾部 \r；malloc 分配 line_len+1 字节并 memcpy；memmove 消费缓冲中已提取数据（包括 \n）。
