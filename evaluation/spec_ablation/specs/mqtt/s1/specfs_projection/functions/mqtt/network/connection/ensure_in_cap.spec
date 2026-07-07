[PROMPT]
Implement function `ensure_in_cap`. Responsibility: 输入缓冲扩容函数：按倍增策略确保 in_data 可容纳指定字节数

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
static bool ensure_in_cap(mqtt_connection_t* c, size_t need);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为连接对象 c 与目标输入缓冲容量 need。

**Post-Condition**:
- 返回 true 表示当前容量已满足或扩容成功；返回 false 表示 realloc 失败且原输入缓冲保持不变。

**Invariant**:
- 只增长 in_cap，不缩小或消费 in_len
- realloc 失败时保留原 in_data/in_cap

**System Algorithm**:
- 当目标容量不超过当前容量时直接返回；否则按倍增策略计算新容量并 realloc 输入缓冲，成功后更新 in_data 与 in_cap。
