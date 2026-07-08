[PROMPT]
Implement function `coap_resource_router_build_core_links`. Responsibility: 按注册顺序为 discoverable entries 构造逗号分隔 link-format 字符串；调用方负责 free

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
char* coap_resource_router_build_core_links(const coap_resource_router_t* router);
```

[SPECIFICATION]
**Pre-Condition**:
- router 是 resource registry。

**Post-Condition**:
- 成功返回新分配 application/link-format 字符串；router 为空或分配失败返回 NULL。调用方负责 free。

**Invariant**:
- 只包含 discoverable=true 的 entries。
- attributes 为 NULL 或空字符串时不输出分号。

**System Algorithm**:
- 若 router 为空返回 NULL。第一遍只统计 discoverable entries：每个 entry 计入 <path> 的 path 长度加 2，attributes 非空时额外计入 ;attributes，多个 link 之间计入 comma。malloc(total + 1) 失败返回 NULL。第二遍按注册顺序输出 discoverable entries：非首个前写 comma，写 <path>，若 attributes 非空且首字符非 NUL，再写 ;attributes，最后写 NUL。没有 discoverable entry 时 total 为 0，成功时返回空字符串。
