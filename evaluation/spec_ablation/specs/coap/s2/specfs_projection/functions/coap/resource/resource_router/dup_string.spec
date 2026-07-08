[PROMPT]
Implement function `dup_string`. Responsibility: 分配并复制 NUL-terminated 字符串；NULL 返回 NULL

[RELY]
None.

[GUARANTEE]
```c
static char* dup_string(const char* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是可空 NUL-terminated string。

**Post-Condition**:
- 成功返回新分配字符串；输入为空或分配失败返回 NULL。调用方负责 free 返回值。

**Invariant**:
- 复制结果包含结尾 NUL。

**System Algorithm**:
- 若 s 为空返回 NULL。否则计算 strlen(s)，malloc(n + 1)，分配失败返回 NULL；成功后 memcpy 包含 NUL terminator 的 n + 1 bytes。
