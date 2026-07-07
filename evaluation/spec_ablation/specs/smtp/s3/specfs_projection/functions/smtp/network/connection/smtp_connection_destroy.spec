[PROMPT]
Implement function `smtp_connection_destroy`. Responsibility: 释放连接对象及输入/输出缓冲，但不主动 close fd

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

[GUARANTEE]
```c
void smtp_connection_destroy(smtp_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(smtp_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；释放本对象持有的内存、连接、队列或子对象，NULL 输入安全返回。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 释放连接对象及输入/输出缓冲，但不主动 close fd。
