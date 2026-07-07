[PROMPT]
Implement function `smtp_auth_default_user`. Responsibility: 返回内置测试用户名 smtpuser

[RELY]
None.

[GUARANTEE]
```c
const char* smtp_auth_default_user(void);
```

[SPECIFICATION]
**Pre-Condition**:
- 无显式参数。

**Post-Condition**:
- 返回静态内置用户名 smtpuser；调用方不得释放返回指针。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 返回内置测试用户名 smtpuser。
