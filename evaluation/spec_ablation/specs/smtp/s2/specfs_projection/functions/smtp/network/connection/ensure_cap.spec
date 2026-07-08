[PROMPT]
Implement function `ensure_cap`. Responsibility: 确保动态字节缓冲容量至少达到 needed，按 4096 起步倍增

[RELY]
None.

[GUARANTEE]
```c
static int ensure_cap(uint8_t** buf, size_t* cap, size_t needed);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：buf(uint8_t**，可为 NULL，BORROWED)；cap(size_t*，可为 NULL，BORROWED)；needed(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达确保动态字节缓冲容量至少达到 needed，按 4096 起步倍增的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。

**System Algorithm**:
- 确保动态字节缓冲容量至少达到 needed，按 4096 起步倍增。
