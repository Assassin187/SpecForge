[PROMPT]
Implement function `http_read_file`. Responsibility: 以二进制方式读取整个文件到动态分配缓冲；调用方负责 free(*out_data)

[RELY]
None.

[GUARANTEE]
```c
int http_read_file(const char* abs_path, char** out_data, size_t* out_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入文件路径和输出指针。

**Post-Condition**:
- 成功返回 0，*out_data 指向堆分配文件内容且 *out_len 为文件字节数；失败返回 -1 并设置 *out_data 为 NULL、*out_len 为 0；调用方只在成功时 free(*out_data)。

**Invariant**:
- 成功时 *out_data 指向堆分配缓冲区，调用方必须 free
- 失败时 *out_data 为 NULL，*out_len 为 0，调用方无需 free
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- fopen rb→fseek SEEK_END→ftell 获取大小→rewind→malloc→fread 全部→fclose→通过 out_data/out_len 输出。
