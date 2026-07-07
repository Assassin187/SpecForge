[PROMPT]
Implement function `try_parse_remaining_length`. Responsibility: 解析 MQTT 可变长 Remaining Length 字段，处理最多 4 字节编码约束

[RELY]
- STRUCT `struct remaining_length`
  role: Remaining Length 解析结果结构体
```c
struct remaining_length;
```

[GUARANTEE]
```c
static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 MQTT 固定头缓冲 buf、长度 buf_len、Remaining Length 起始偏移 start 与输出对象 out。

**Post-Condition**:
- 完整解析 1 到 4 字节 Remaining Length 时返回 true 并写入 value/bytes；缓冲不足或超过 4 字节仍未结束时返回 false。

**Invariant**:
- 不消费输入缓冲
- 遵守 MQTT Remaining Length 的 7bit+续位编码上限

**System Algorithm**:
- 按 MQTT 可变长编码逐字节解析 Remaining Length，输出 value 与 bytes。
