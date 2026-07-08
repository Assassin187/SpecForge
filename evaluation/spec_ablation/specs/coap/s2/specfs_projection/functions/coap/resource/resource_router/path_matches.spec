[PROMPT]
Implement function `path_matches`. Responsibility: 执行 exact match 或 segment-boundary prefix match

[RELY]
- STRUCT `coap_resource_entry_t`
  role: 本函数使用的模块内部数据结构
```c
typedef struct coap_resource_entry coap_resource_entry_t;
```

[GUARANTEE]
```c
static bool path_matches(const coap_resource_entry_t* e, const char* path);
```

[SPECIFICATION]
**Pre-Condition**:
- e 是 resource entry；path 是 normalized request path。

**Post-Condition**:
- exact match 或 segment-boundary prefix match 成功返回 true；空参数、前缀不匹配或边界不满足返回 false。

**Invariant**:
- prefix route /kv 能匹配 /kv 和 /kv/<suffix>，但不能匹配 /kvx。

**System Algorithm**:
- 若 e 或 path 为空返回 false。若 e->prefix_match 为 false，只在 strcmp(e->path, path) == 0 时返回 true。若 prefix_match 为 true，先要求 path 前 strlen(e->path) bytes 与 e->path 相同；再要求 path 在该位置结束或下一个字符为 /，从而保证 segment boundary。
