[PROMPT]
Implement function `kv_put`. Responsibility: 创建或更新 KV entry，复制 payload 与 Content-Format，并通过 created 报告是否新建

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

- FUNC `find_kv`
  role: 调用 find_kv 完成子步骤
```c
static kv_entry_t* find_kv(coap_server_t* s, const char* key);
```

- FUNC `dup_string`
  role: 调用 dup_string 完成子步骤
```c
static char* dup_string(const char* s);
```

[GUARANTEE]
```c
static bool kv_put(coap_server_t* s, const char* key, const uint8_t* value, size_t len, bool has_content_format, uint16_t content_format, bool* created);
```

[SPECIFICATION]
**Pre-Condition**:
- s/key 指定目标 store 与 key；value/len 是要保存的 payload；has_content_format/content_format 是 metadata；created 可为空。

**Post-Condition**:
- 创建或更新成功返回 true；server/key 无效、entry 数组扩展失败、key 复制失败或 value 复制失败返回 false。created 仅在新 entry 成功加入时为 true。

**Invariant**:
- 更新现有 entry 时，只有新 value copy 分配成功后才释放旧 value。
- len 为 0 时保存 NULL value 且 value_len 为 0。

**System Algorithm**:
- 若 created 非空，先写 *created=false。若 s 或 key 为空返回 false。调用 find_kv 查找现有 entry；未命中时 realloc kv_entries 到 kv_count+1，失败返回 false；清零新 entry，复制 key，key 复制失败返回 false；成功后 kv_count 加 1，并在 created 非空时写 true。随后若 len > 0，malloc(len) 并复制 value；分配失败返回 false，已有 entry 的旧 value 保持未替换。成功后释放 entry->value，写入 copy、value_len、has_content_format 和 content_format。
