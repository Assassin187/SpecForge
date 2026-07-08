[PROMPT]
Implement function `kv_delete`. Responsibility: 删除匹配 KV entry，释放字段并压紧数组；未命中返回 false

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- STRUCT `kv_entry_t`
  role: 本函数使用的模块内部数据结构
```c
typedef struct kv_entry kv_entry_t;
```

[GUARANTEE]
```c
static bool kv_delete(coap_server_t* s, const char* key);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是 server；key 是待删除 key。

**Post-Condition**:
- 删除成功返回 true；参数为空或未命中返回 false。

**Invariant**:
- 删除保持剩余 entries 的相对顺序。
- 最后一个 entry 删除后数组指针被复位为 NULL。

**System Algorithm**:
- 若 s 或 key 为空返回 false。线性扫描 kv_entries；不匹配则继续。命中时释放 entry.key 和 entry.value；若后面还有元素，用 memmove 将尾部压紧；kv_count 减 1；若 kv_count 变为 0，释放 kv_entries 并置 NULL。
