[PROMPT]
Implement function `ensure_sid_cap`. Responsibility: 扩容单条 filter 的会话 ID 数组

[RELY]
- STRUCT `struct filter_entry`
  role: 过滤器条目结构体
```c
struct filter_entry;
```

[GUARANTEE]
```c
static void ensure_sid_cap(filter_entry_t* e, size_t need);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 filter_entry e 与目标 sid 容量 need。

**Post-Condition**:
- e 为空或容量已足够时直接返回；扩容成功时 sids 和 sid_cap 更新到覆盖 need；realloc 失败时保持原数组不变。

**Invariant**:
- 只增长 sid_cap
- 不改变 sid_count

**System Algorithm**:
- 若条目 sid 容量不足则按倍增策略 realloc sids。
