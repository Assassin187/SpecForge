[PROMPT]
Implement function `parse_port`. Responsibility: 解析命令行端口字符串，接受 1..65535，非法返回 0

[RELY]
None.

[GUARANTEE]
```c
static uint16_t parse_port(const char* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：s(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回合法端口号；空输入、非数字尾部、0 或超过 65535 时返回 0。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 使用 strtol 完整解析十进制端口；拒绝空输入、非数字尾部、0 和超过 65535 的值。
