[PROMPT]
Implement function `coap_resource_router_destroy`. Responsibility: 释放每个 entry 的 path/attributes、entry 数组与 router

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

[GUARANTEE]
```c
void coap_resource_router_destroy(coap_resource_router_t* router);
```

[SPECIFICATION]
**Pre-Condition**:
- router 是可能为空的 resource router。

**Post-Condition**:
- 无返回值；非空 router 及其拥有的 entry 字符串被释放。

**Invariant**:
- handler 和 user 是借用引用，不在 destroy 中释放。

**System Algorithm**:
- 若 router 为空直接返回。否则遍历 entries[0..count)，释放每个 entry 的 path 和 attributes；随后释放 entries 数组与 router 本身。
