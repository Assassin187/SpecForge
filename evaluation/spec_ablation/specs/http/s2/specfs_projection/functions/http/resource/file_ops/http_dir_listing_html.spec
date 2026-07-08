[PROMPT]
Implement function `http_dir_listing_html`. Responsibility: 生成目录 HTML 列表页面：opendir→readdir→stat→格式化文件名/大小/目录标记；调用方负责 free

[RELY]
None.

[GUARANTEE]
```c
char* http_dir_listing_html(const char* abs_dir, const char* uri_prefix);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入目录绝对路径和 URI 前缀。

**Post-Condition**:
- 成功返回堆分配 HTML 字符串；opendir、分配或格式化失败返回 NULL；调用方只在成功时 free 返回值。

**Invariant**:
- 成功时返回堆分配字符串，调用方必须 free
- 失败时返回 NULL，调用方无需 free
- . 和 .. 必须从列表中排除
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- opendir→循环 readdir→跳过 . 和 ..→stat 获取大小和类型→realloc 扩展 HTML 缓冲→snprintf 追加行（文件名、大小 B/KB/MB、[DIR] 标记）→closedir。
