[PROMPT]
Implement function `entry_remove_sid`. Responsibility: 从条目中删除指定 session_id（末尾覆盖）

[RELY]
- STRUCT `struct filter_entry`
  role: 过滤器条目结构体
```c
struct filter_entry;
```

[GUARANTEE]
```c
static void entry_remove_sid(filter_entry_t* e, int sid);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 filter_entry e 与待删除 session id。

**Post-Condition**:
- e 为空或 sid 不存在时直接返回；命中时用末尾 sid 覆盖当前位置并将 sid_count 减一。

**Invariant**:
- 删除操作不保持 sid 顺序
- 只修改命中的一个 sid

**System Algorithm**:
- 命中 sid 后用末元素覆盖并缩减 sid_count。
