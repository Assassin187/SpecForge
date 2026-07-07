[PROMPT]
Implement function `ensure_dir_recursive`. Responsibility: 递归创建 root_dir 路径中的各级目录

[RELY]
- FUNC `mkdir`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
static int ensure_dir_recursive(const char* path);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：path(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0；路径无效、过长或 mkdir 失败时返回 -1 并保留 errno。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 递归创建 root_dir 路径中的各级目录。
