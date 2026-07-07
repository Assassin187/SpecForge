[PROMPT]
Implement function `smtp_connection_fd`. Responsibility: 读取连接 fd；conn 为 NULL 时返回 -1

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

[GUARANTEE]
```c
int smtp_connection_fd(const smtp_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(const smtp_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回对象保存的 fd；对象为 NULL 时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->fd：读取 fd

**System Algorithm**:
- 读取连接 fd；conn 为 NULL 时返回 -1。
