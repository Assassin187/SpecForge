[PROMPT]
Implement function `smtp_mail_store_write`. Responsibility: 生成唯一 .eml 文件并写入 envelope headers、空行和 DATA 内容

[RELY]
- STRUCT `smtp_mail_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_mail {
    const char* helo_name;
    const char* auth_user;
    const char* mail_from;
    const char* const* rcpt_to;
    size_t rcpt_count;
    const char* data;
    size_t data_len;
} smtp_mail_t;
```

- FUNC `ensure_dir_recursive`
  role: 被该函数调用以完成子步骤
```c
static int ensure_dir_recursive(const char* path);
```

- FUNC `open`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `write_all`
  role: 被该函数调用以完成子步骤
```c
static int write_all(int fd, const void* data, size_t len);
```

- FUNC `close`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `unlink`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `snprintf`
  role: 格式化最终 .eml 文件路径和 envelope headers
  declaration: external dependency; canonical declaration unavailable.

- FUNC `time`
  role: 参与构造唯一文件名
  declaration: external dependency; canonical declaration unavailable.

- FUNC `getpid`
  role: 参与构造唯一文件名
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int smtp_mail_store_write(const char* root_dir, const smtp_mail_t* mail, char* out_path, size_t out_path_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：root_dir(const char*，可为 NULL，BORROWED)；mail(const smtp_mail_t*，可为 NULL，BORROWED)；out_path(char*，可为 NULL，BORROWED)；out_path_len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0，最终 .eml 文件已经存在且内容完整；out_path 非 NULL 时指向该实际文件，out_path 为 NULL 时仍可成功。参数无效、目录创建失败、16 次唯一文件创建均冲突、路径/输出缓冲不足、写入或关闭失败时返回 -1，并确保本次未完成文件不残留。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 smtp_mail_t.helo_name：HELO/EHLO 名称
- 维护 smtp_mail_t.auth_user：认证用户
- 维护 smtp_mail_t.mail_from：envelope sender
- 维护 smtp_mail_t.rcpt_to：recipient 指针数组
- 维护 smtp_mail_t.rcpt_count：recipient 数量
- 维护 smtp_mail_t.data：DATA 内容
- 维护 smtp_mail_t.data_len：DATA 长度
- 维护 mail->rcpt_to[i]：写入每个 X-SMTP-Envelope-To header
- 遵循 SMTP_MAIL_FILE.envelope_headers 映射规则：写入 X-SMTP-HELO/X-SMTP-Auth-User/X-SMTP-Envelope-From 和每个 X-SMTP-Envelope-To
- 遵循 SMTP_MAIL_FILE.message_data 映射规则：空行后写入 mail->data 长度为 mail->data_len 的原始 DATA
- 只直接创建带 .eml 后缀的最终路径，不维护需要 rename 的第二路径
- 唯一性由 O_EXCL 与最多 16 次 counter 重试保证，不能覆盖已有邮件
- 返回 0 前 fd 已成功关闭，失败路径删除本次已创建但未完成的文件

**System Algorithm**:
- root_dir、mail、mail->mail_from、mail->rcpt_to 或 mail->data 无效时返回 -1；out_path 允许为 NULL，仅在非 NULL 时要求 out_path_len 足以保存路径。先确保 root_dir 存在，再用 time(NULL)、getpid() 和单线程单调 counter 格式化最终的 '<root_dir>/mail_<time>_<pid>_<counter>.eml' 路径，并使用 open(path, O_WRONLY | O_CREAT | O_EXCL, 0644) 直接创建最终 .eml 文件；EEXIST 时递增 counter 并重试，最多 16 次。禁止使用 mkstemp、mkstemps 或 rename，禁止先创建无后缀文件再原地修改路径字符串。创建成功后依次写入 HELO/Auth/Envelope-From headers、每个 recipient 的 Envelope-To header、空行和严格为 mail->data_len 字节的 DATA。任一格式化、write_all 或 close 失败都关闭仍打开的 fd、unlink 当前最终路径并返回 -1。只有完整写入并成功 close 后，才在 out_path 非 NULL 时复制实际路径并返回 0。
