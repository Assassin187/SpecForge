[PROMPT]
Implement function `coap_resource_router_add`. Responsibility: 扩展 entry 数组并复制 path/attributes，保存匹配策略、method mask、handler 与 user

[RELY]
- STRUCT `coap_resource_router_t`
  role: 资源注册表与 dispatch 逻辑的不透明句柄
```c
typedef struct coap_resource_router coap_resource_router_t;
```

- STRUCT `coap_resource_entry_t`
  role: 本函数使用的模块内部数据结构
```c
typedef struct coap_resource_entry coap_resource_entry_t;
```

- FUNC `dup_string`
  role: 调用 dup_string 完成子步骤
```c
static char* dup_string(const char* s);
```

[GUARANTEE]
```c
bool coap_resource_router_add(coap_resource_router_t* router, const char* path, bool prefix_match, uint32_t methods_mask, bool discoverable, const char* attributes, coap_resource_handler_fn handler, void* user);
```

[SPECIFICATION]
**Pre-Condition**:
- router/path/handler 必须非空；prefix_match、methods_mask、discoverable、attributes、user 描述要注册的 resource entry。

**Post-Condition**:
- 成功注册 entry 返回 true；必要参数为空、entries 扩展失败或 path 复制失败返回 false。

**Invariant**:
- router 拥有复制后的 path/attributes。
- entry 按注册顺序追加，dispatch 也按该顺序匹配。

**System Algorithm**:
- 若 router、path 或 handler 为空返回 false。用 realloc 将 entries 扩展到 count + 1；失败返回 false。清零新 entry，复制 path，复制 attributes；attributes 为空时复制结果为 NULL。若 path 复制失败，释放已复制 attributes 并返回 false。成功后写入 prefix_match、methods_mask、discoverable、handler、user，并递增 count。源码不把非空 attributes 复制失败视为 add 失败。
