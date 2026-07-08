[PROMPT]
Implement function `smtp_mail_store_init`. Responsibility: 初始化邮件根目录，等价调用 ensure_dir_recursive

[RELY]
- FUNC `ensure_dir_recursive`
  role: 被该函数调用以完成子步骤
```c
static int ensure_dir_recursive(const char* path);
```

[GUARANTEE]
```c
int smtp_mail_store_init(const char* root_dir);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：root_dir(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回 ensure_dir_recursive(root_dir) 的结果：目录可用为 0，失败为 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。

**System Algorithm**:
- 初始化邮件根目录，等价调用 ensure_dir_recursive。
