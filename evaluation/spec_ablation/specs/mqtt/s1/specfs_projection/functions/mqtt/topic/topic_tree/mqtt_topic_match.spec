[PROMPT]
Implement function `mqtt_topic_match`. Responsibility: 按 MQTT 通配符规则匹配过滤器与主题

[RELY]
- FUNC `next_level`
  role: 按层级切分并比较 filter/topic
```c
static bool next_level(const char* s, size_t s_len, size_t* io_pos, const char** out_start, size_t* out_len, bool* out_done);
```

[GUARANTEE]
```c
bool mqtt_topic_match(const char* filter, const char* topic);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为过滤器 filter 与主题 topic

**Post-Condition**:
- 返回 true 表示匹配成功，false 表示不匹配或参数非法

**Invariant**:
- # 仅在最后一层合法
- 匹配过程不修改输入字符串
- interop/topic 匹配 interop/topic
- interop/topic 不匹配 interop/topic/x
- a/# 匹配 a/b
- a/+ 匹配 a/b
- a/+ 不匹配 a/b/c
- a/#/x 非法或不得匹配任何 topic

**System Algorithm**:
- 逐层解析 filter/topic 并比较：普通层级必须 exact match；+ 必须作为完整 filter level 且只匹配一个 topic level；# 必须作为完整且最后一个 filter level，并立即匹配剩余 topic。普通层全部匹配后，只有 filter 与 topic 同时消费完才返回 true；不得把已结束后的空 segment 当作有效匹配层。
