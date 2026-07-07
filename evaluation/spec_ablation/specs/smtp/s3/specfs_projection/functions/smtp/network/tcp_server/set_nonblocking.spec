[PROMPT]
Implement function `set_nonblocking`. Responsibility: 给 fd 增加 O_NONBLOCK 标志

[RELY]
- FUNC `fcntl`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
static int set_nonblocking(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达给 fd 增加 O_NONBLOCK 标志的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 给 fd 增加 O_NONBLOCK 标志。
