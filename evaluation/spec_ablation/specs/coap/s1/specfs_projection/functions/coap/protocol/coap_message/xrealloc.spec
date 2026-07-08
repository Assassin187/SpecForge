[PROMPT]
Implement function `xrealloc`. Responsibility: 调用 realloc 调整内部动态数组容量

[RELY]
None.

[GUARANTEE]
```c
static void* xrealloc(void* p, size_t n);
```

[SPECIFICATION]
**Pre-Condition**:
- p 是现有动态缓冲或 NULL；n 是请求的新字节数。

**Post-Condition**:
- 返回 realloc 的结果；失败时返回 NULL 且原指针仍由 C library 规则保留。

**Invariant**:
- 该 helper 不释放失败时的原始 p，也不清零新增长区域。

**System Algorithm**:
- 直接调用 realloc(p, n)，不添加额外检查或初始化。
