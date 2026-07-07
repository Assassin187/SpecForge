[PROMPT]
Implement function `smtp_connection_create`. Responsibility: 为已建立 fd 创建 smtp_connection_t 并初始化缓冲状态

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

[GUARANTEE]
```c
smtp_connection_t* smtp_connection_create(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回新分配并初始化的对象指针；分配或依赖初始化失败返回 NULL，并清理已分配资源。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->fd：写入传入 fd

**System Algorithm**:
- 为已建立 fd 创建 smtp_connection_t 并初始化缓冲状态。
