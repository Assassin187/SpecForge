[PROMPT]
Implement function `http_stat_path`. Responsibility: 对路径执行 stat；返回 0=普通文件, 1=目录, -1=不存在/无权限

[RELY]
None.

[GUARANTEE]
```c
int http_stat_path(const char* abs_path);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入绝对路径。

**Post-Condition**:
- 返回路径类型判别码；stat 失败返回 -1 只表示路径不存在、无权限或其他 stat 错误，调用方必须结合 http_resolve_path 的结果分类。对 serve_file 而言，已安全 resolve 的 missing leaf 应映射为 404 Not Found。

**Invariant**:
- S_ISREG 与 S_ISDIR 互斥，优先级 S_ISREG 先于 S_ISDIR
- stat 失败时返回 -1，不修改全局状态
- http_resolve_path 已确认路径位于 root 内时，serve_file 应把 stat 失败映射为 404，而不是 403
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。

**System Algorithm**:
- stat 获取文件信息→S_ISREG 返回 0→S_ISDIR 返回 1→其他返回 -1。
