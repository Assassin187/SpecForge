[PROMPT]
Implement function `smtp_response_format_ehlo_caps`. Responsibility: 格式化 EHLO capability 多行响应，声明 AUTH LOGIN PLAIN、SIZE 和 8BITMIME

[RELY]
None.

[GUARANTEE]
```c
int smtp_response_format_ehlo_caps(char* out, size_t out_len, const char* hostname, size_t max_size);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：out(char*，可为 NULL，BORROWED)；out_len(size_t，不可为 NULL，BORROWED)；hostname(const char*，可为 NULL，BORROWED)；max_size(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回写入多行 EHLO capabilities 的字节数；参数无效或 out_len 不足时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 遵循 SMTP_RESPONSE.ehlo_capabilities 映射规则：前三行使用 250- continuation，最后一行使用 250 空格终止

**System Algorithm**:
- 格式化 EHLO capability 多行响应，声明 AUTH LOGIN PLAIN、SIZE 和 8BITMIME。
