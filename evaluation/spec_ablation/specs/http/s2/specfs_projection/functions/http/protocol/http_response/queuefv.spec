[PROMPT]
Implement function `queuefv`. Responsibility: 静态辅助：vsnprintf 格式化到栈缓冲后委托 http_connection_queue_str

[RELY]
- FUNC `http_connection_queue_str`
  role: 排队字符串到连接输出缓冲
```c
int http_connection_queue_str(http_connection_t* conn, const char* text);
```

[GUARANTEE]
```c
static int queuefv(http_connection_t* conn, const char* fmt, ...);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 conn、格式字符串及可变参数。

**Post-Condition**:
- 成功返回 http_connection_queue_str 结果，溢出返回 -1。

**Invariant**:
- 栈缓冲上限 4096 字节
- 仅作为 http_response_send 的内部辅助函数使用
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- conn 为已创建的有效连接对象
- fmt 与 args 构成有效的 printf 格式字符串与参数
- 格式化后的字符串已追加到 conn 的输出缓冲
- 返回 0 表示成功，返回 -1 表示格式化或队列操作失败

**System Algorithm**:
- vsnprintf 格式化到 4096 字节栈缓冲→http_connection_queue_str 排队结果。溢出返回 -1。
