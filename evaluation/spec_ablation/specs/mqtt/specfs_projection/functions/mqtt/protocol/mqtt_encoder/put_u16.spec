[PROMPT]
Implement function `put_u16`. Responsibility: 按网络字节序写入 16 位无符号整数并推进写游标

[RELY]
None.

[GUARANTEE]
```c
static void put_u16(uint8_t* out, size_t* pos, uint16_t v);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为可写输出缓冲 out、游标 pos 与 16 位整数 v。

**Post-Condition**:
- 将 v 的高字节、低字节依次写入 out，并把 *pos 推进 2。

**Invariant**:
- 调用方保证 out 剩余空间足够
- 写入格式为网络字节序

**System Algorithm**:
- 将 16 位整数按高字节在前写入输出缓冲并推进位置。
