[PROMPT]
Implement function `find_kv`. Responsibility: 按 key 线性查找内存 KV entry；未命中返回 NULL

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
static kv_entry_t* find_kv(coap_server_t* s, const char* key);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是 server；key 是待查找 key。

**Post-Condition**:
- 命中时返回 entry 指针；未命中或参数为空返回 NULL。返回指针借用 server 内部数组，不能由调用方释放。

**Invariant**:
- 只返回第一个 key 完全相等的 entry。

**System Algorithm**:
- 若 s 或 key 为空返回 NULL。否则从 i=0 到 kv_count-1 线性扫描 kv_entries，用 strcmp(entry.key, key) 判断命中。
