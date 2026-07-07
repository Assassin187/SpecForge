[PROMPT]
Implement function `http_resolve_path`. Responsibility: 将 root_dir 和 uri_path 解析为 document root 内的 canonical 绝对路径；使用目录边界而非普通字符串前缀判断归属，leaf 不存在但 canonical parent 安全时仍返回 0，目录遍历或 root escape 返回 -1+EACCES

[RELY]
None.

[GUARANTEE]
```c
int http_resolve_path(const char* root_dir, const char* uri_path, char* out_abs, size_t out_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 root_dir、uri_path、输出缓冲区。

**Post-Condition**:
- 成功返回 0 并通过 out_abs 输出 canonical document root 本身、已存在对象的 canonical 绝对路径，或 canonical parent 加单个 missing leaf 的安全绝对路径；root 内 immediate missing leaf 返回 0，供调用方通过 http_stat_path 映射为 404；traversal、root escape、非 ENOENT 的解析失败或 unresolvable parent 返回 -1 且 errno=EACCES。

**Invariant**:
- real_root 必须直接使用 realpath(root_dir) 的 canonical 输出；除 real_root 为 '/' 外，不得为了比较而人为添加尾部 '/'
- 对非根目录 real_root，inside_root(path, real_root) 当且仅当 strcmp(path, real_root)==0，或 strncmp(path, real_root, strlen(real_root))==0 且 path[strlen(real_root)]=='/'；real_root 为 '/' 时，canonical absolute path 均位于该 root 内
- 禁止仅使用 strncmp(path, real_root, strlen(real_root)) 判断目录归属，否则会把同名字符串前缀误判为 root 子路径
- 参与目录归属比较的 path 和 real_root 必须都是 realpath 产生的 canonical 形式，且采用一致的尾斜杠形式
- 每个普通 URI segment 最多追加一次；missing leaf 必须先从 candidate 拆出，再在 canonical parent 后仅拼接一次
- 目录遍历攻击必须被拒绝并设置 errno=EACCES
- root 内 leaf 不存在不是安全错误，必须让调用方后续通过 http_stat_path 映射为 404
- realpath 失败时必须区分 missing leaf 与 parent/root 不安全
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 按固定顺序解析路径：第一步调用 realpath(root_dir, real_root)，real_root 保持 realpath 返回的 canonical 形式，除 real_root 本身为 '/' 外禁止人为追加尾部 '/'。第二步只对 uri_path 做 lexical segment normalization：跳过空段和 '.'；普通 segment 按出现顺序压入相对路径且每段只追加一次；'..' 弹出一层，在相对路径已为空时遇到 '..' 立即以 EACCES 失败。第三步仅构造一次 candidate：相对路径为空时 candidate=real_root，否则 candidate=real_root+'/'+normalized_relative_path。第四步调用 realpath(candidate, resolved)：成功时按严格目录边界验证 resolved 位于 real_root 内并输出 resolved。若且仅若 candidate 因 ENOENT 不存在，则从已规范化的 candidate 中拆出 immediate parent 与 leaf，leaf 不得再次参与 segment normalization；调用 realpath(parent, real_parent)，按同一目录边界验证 real_parent，然后仅追加一次 '/'+leaf 到 real_parent 并输出。candidate 的其他 realpath 错误、parent 不可解析、目录遍历、root escape 或输出过长时分别设置 errno=EACCES 或 ENAMETOOLONG 并返回 -1。
