[PROMPT]
Implement function `entry_has_sid`. Responsibility: 判断条目是否已包含指定 session_id

[RELY]
- STRUCT `struct filter_entry`
  role: 过滤器条目结构体
```c
struct filter_entry;
```

[GUARANTEE]
```c
static bool entry_has_sid(const filter_entry_t* e, int sid);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 filter_entry e 与 session id。

**Post-Condition**:
- e 为空时返回 false；sid 已存在于 e->sids[0..sid_count) 时返回 true，否则返回 false。

**Invariant**:
- 不修改条目
- 只检查有效 sid_count 范围

**System Algorithm**:
- 遍历条目 sid 数组判断指定 session_id 是否存在。
