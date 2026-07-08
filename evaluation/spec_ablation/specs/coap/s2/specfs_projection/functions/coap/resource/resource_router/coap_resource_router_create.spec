[PROMPT]
Implement function `coap_resource_router_create`. Responsibility: 分配空 resource router

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
coap_resource_router_t* coap_resource_router_create(void);
```

[SPECIFICATION]
**Pre-Condition**:
- 无输入参数。

**Post-Condition**:
- 成功返回空 router；分配失败返回 NULL。

**Invariant**:
- 新 router 的 entries 为 NULL，count 为 0。

**System Algorithm**:
- 调用 calloc(1, sizeof(coap_resource_router_t)) 分配零初始化 router。
