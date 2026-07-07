[PROMPT]
Implement function `mqtt_topic_tree_match_subscribers`. Responsibility: 匹配主题对应的订阅会话并输出去重列表

[RELY]
- STRUCT `struct mqtt_topic_tree`
  role: 主题树私有结构体
```c
struct mqtt_topic_tree;
```

- STRUCT `struct filter_entry`
  role: 过滤器条目结构体
```c
struct filter_entry;
```

- FUNC `mqtt_topic_match`
  role: 判断过滤器是否匹配主题
```c
bool mqtt_topic_match(const char* filter, const char* topic);
```

[GUARANTEE]
```c
bool mqtt_topic_tree_match_subscribers(const mqtt_topic_tree_t* t, const char* topic, int** out_sids, size_t* out_count);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t、主题 topic、输出数组 out_sids 与数量 out_count

**Post-Condition**:
- 返回 true 表示匹配完成（含 0 命中），false 表示参数或内存失败

**Invariant**:
- 输出 sid 列表不包含重复值
- exact filter 与 identical topic 命中时，订阅 sid 必须出现在输出列表中
- 内存扩容失败时会释放临时数组避免泄漏

**System Algorithm**:
- 先将输出置空；参数非法返回 false；遍历所有 filter，调用 mqtt_topic_match 判定命中；聚合 sid 并去重，按需扩容 uniq 数组；成功后写回输出
