[PROMPT]
Implement function `ensure_cap`. Responsibility: 按目标元素数量扩展会话数组容量，保证后续插入可进行

[RELY]
- STRUCT `struct mqtt_session_manager`
  role: 会话管理器私有结构
```c
struct mqtt_session_manager;
```

[GUARANTEE]
```c
static void ensure_cap(mqtt_session_manager_t* m, size_t need);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为会话管理器 m 与所需最小容量 need

**Post-Condition**:
- 无返回值；成功时 m->cap 增长到可容纳 need，失败时容量不变

**Invariant**:
- 扩容失败不会破坏原 sessions 指针与已有元素
- 扩容策略单调递增并保证新 cap >= need

**System Algorithm**:
- 若 m 为空或 need<=cap 则直接返回；否则以 cap*2（初始 16）做倍增直到覆盖 need，调用 realloc 扩容 sessions；realloc 成功则更新 sessions 与 cap，失败保持原状态不变
