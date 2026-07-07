[PROMPT]
Implement function `ensure_cap`. Responsibility: 缓冲扩容函数：从 4096 起倍增直到满足 needed 字节需求

[RELY]
None.

[GUARANTEE]
```c
static int ensure_cap(uint8_t** buf, size_t* cap, size_t needed);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：buf(uint8_t**，不可为 NULL，BORROWED)；cap(size_t*，不可为 NULL，BORROWED)；needed(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0，realloc 失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 *buf：通过 realloc 更新为扩容后的缓冲指针
- 维护 *cap：更新为扩容后的新容量

**System Algorithm**:
- 若 needed <= *cap 直接返回 0；初始容量从 4096 开始，倍增直到 >= needed；realloc 分配新缓冲，更新 *buf 和 *cap。
