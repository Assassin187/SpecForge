[PROMPT]
Implement function `smtp_response_format`. Responsibility: 格式化单行 SMTP 响应为 '<code> <text>\r\n'

[RELY]
None.

[GUARANTEE]
```c
int smtp_response_format(char* out, size_t out_len, int code, const char* text);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：out(char*，可为 NULL，BORROWED)；out_len(size_t，不可为 NULL，BORROWED)；code(int，不可为 NULL，BORROWED)；text(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回写入响应行的字节数；参数无效、snprintf 失败或 out_len 不足时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 遵循 SMTP_RESPONSE.status_line 映射规则：输出格式固定为三位/整数 code、空格、text、CRLF

**System Algorithm**:
- 格式化单行 SMTP 响应为 '<code> <text>\r\n'。
