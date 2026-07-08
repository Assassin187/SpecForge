[PROMPT]
Implement function `b64_value`. Responsibility: 把单个 base64 字符映射为 6-bit 值，'=' 返回 padding 标记

[RELY]
None.

[GUARANTEE]
```c
static int b64_value(char c);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：c(char，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回 0..63 的 Base64 6-bit 值；'=' 返回 -2 表示 padding；非法字符返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。

**System Algorithm**:
- 把单个 base64 字符映射为 6-bit 值，'=' 返回 padding 标记。
