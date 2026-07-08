[PROMPT]
Implement function `smtp_connection_has_pending`. Responsibility: 判断输出队列是否仍有未发送字节

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

[GUARANTEE]
```c
bool smtp_connection_has_pending(const smtp_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(const smtp_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回输出队列是否仍有待发送字节；对象为 NULL 时返回 false。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 维护 conn->out_off：已发送偏移
- 维护 conn->out_len：输出总长度

**System Algorithm**:
- 判断输出队列是否仍有未发送字节。
