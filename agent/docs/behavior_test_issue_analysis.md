# 协议行为测试问题分析报告

> 生成日期：2026-06-16
> 范围：CoAP、HTTP/1.1、MQTT、SMTP 四个协议通过 coder agent 从示例 specs 生成代码后的行为验证结果

---

## 一、总体情况

| 协议 | 测试总数 | 通过 | 失败 | 已修复 | 编译轮数 |
|------|----------|------|------|--------|----------|
| CoAP | 6 | 6 | 0 | 1（测试脚本） | 0~1 |
| HTTP | 9 | 6 | 3 | 1（serve_file 宏误用） | 1 |
| MQTT | 6 | 6 | 0 | 0 | 0~1 |
| SMTP | 8 | 5 | 3 | 1（queue_code 返回值） | 1 |

---

## 二、已修复的问题

### 2.1 CoAP — `coap_malformed_survival`：畸形包后正常请求失败

- **现象**：发送垃圾 UDP 数据报后，后续合法 GET /hello 返回 `code=0` 而非 2.05
- **直接原因**：服务器按 RFC 7252 对畸形包正确发送 RST（Reset），RST 数据报残留在测试客户端的 UDP socket 接收缓冲区中。后续合法请求的 `recvfrom()` 读到了旧 RST 而非新响应
- **根因**：测试脚本 `protocol_behavior_val/coap.py` 在发送垃圾包后未排空 socket 缓冲区
- **层级**：**测试脚本**
- **修复**：在发送垃圾包后、发送合法请求前，循环 `recvfrom()` 排空陈旧数据报

### 2.2 HTTP — `http_tcp_connect`：GET /hello 返回 403

- **现象**：首个测试 TCP 连接 + GET /hello，期望 200，实际 403
- **直接原因**：`serve_file` 对 `http_stat_path` 的返回值（0=文件，1=目录，-1=错误）错误地应用了 `S_ISREG()`/`S_ISDIR()` 宏，而这两个宏期望的是原始 `mode_t` 位掩码。`S_ISREG(0)` 和 `S_ISDIR(1)` 均求值为 false，所有请求落入 `else` 分支返回 403
- **根因**：LLM 在分别生成 `http_stat_path`（file_ops.c）和 `serve_file`（http_server.c）时，两个函数对返回值语义的理解不一致——前者返回抽象类型码，后者将其当作原始 stat 模式
- **层级**：**Coder（跨文件返回值语义不匹配）**
- **修复**：通过 prompt 增强（`_callee_return_info`）自动向调用方注入被调用函数的返回值语义信息

### 2.3 SMTP — `smtp_tcp_connect`：TCP 连接后超时无 220 问候

- **现象**：TCP 连接建立，服务器超时不发送 220 服务问候横幅
- **直接原因**：`queue_code` 调用 `smtp_response_format`，后者返回正数字节数（27）表示成功，但 `queue_code` 用 `if (ret != 0)` 检查，将正数返回值误判为错误，提前返回，从未调用 `queue_raw` 将 220 排入输出队列
- **根因**：LLM 生成的 `smtp_response_format`（smtp_response.c）使用 `>0=字节数, <0=错误` 的返回约定（来自 snprintf），但 `queue_code`（smtp_server.c）使用 `0=成功, !=0=错误` 的返回约定，两个函数对 `int` 返回值的语义约定不一致
- **层级**：**Coder（跨文件返回值语义不匹配）**
- **修复**：通过 prompt 增强 + CALL_CONTRACTS 填充

---

## 三、当前仍存在的问题

### 3.1 HTTP — `http_404_not_found`：不存在的路径返回 403 而非 404

- **现象**：GET /nonexistent 期望 404 Not Found，实际 403 Forbidden
- **直接原因**：`serve_file` 中先调 `http_resolve_path` 做路径解析+安全校验，失败则直接返回 403；再调 `http_stat_path` 检查文件存在性，失败才返回 404。`http_resolve_path` 内部调用 `realpath()`，该函数要求目标路径必须实际存在才能解析成功。`/nonexistent` 不存在 → `realpath()` 失败 → `http_resolve_path` 返回 -1 → `serve_file` 返回 403，**永远执行不到第二步的 404 检查**
- **根因**：`http_resolve_path` 将"安全沙箱校验（路径是否在 root 内）"和"路径存在性检查"耦合在同一个 `realpath()` 调用中。`serve_file` 无法区分 `http_resolve_path` 的两种失败原因——是路径穿越攻击（→403）还是文件不存在（→404）
- **层级**：**Coder（函数职责边界设计不当）**
- **涉及代码**：`http_server.c:serve_file`、`file_ops.c:http_resolve_path`

### 3.2 HTTP — `http_501_unknown_method`：DELETE 方法返回 400 而非 501

- **现象**：发送 `DELETE /hello HTTP/1.1`，期望 501 Not Implemented，实际 400 Bad Request
- **直接原因**：调用链 `on_event_cb → http_request_parse → parse_method("DELETE") → HTTP_UNKNOWN`，然后 `http_request_parse` 中 `if (req->method == HTTP_UNKNOWN) return -1;` 将"未知方法"当作解析错误。`on_event_cb` 中 `parse_ret == -1` 分支直接返回 400，绕过了 `dispatch` 中正确的 `default: send_error(501)`
- **根因**：HTTP 请求解析的语义分层错误——`parse_method` 正确将 DELETE 映射为 `HTTP_UNKNOWN`（语法正确但语义未知），但 `http_request_parse` 错误地将 `HTTP_UNKNOWN` 归类为解析失败（-1），而非将"语法合法的请求但方法未知"传递给上层路由逻辑处理
- **层级**：**Coder（解析器错误分类逻辑）**
- **涉及代码**：`http_request.c:http_request_parse`、`http_request.c:parse_method`

### 3.3 HTTP — `http_response_headers`：响应缺少 Content-Type 头

- **现象**：响应中缺少 `Content-Type:` 头
- **直接原因**：`serve_file` 传递 `extra_headers = {"Content-Type", "application/octet-stream", NULL}`（name/value 分离的格式），但 `http_response_send` 按"每个元素是已格式化的 `"Name: value"` 字符串"来处理——对每个元素只追加 `\r\n` 而不插入 `: ` 分隔符。实际发送的内容为 `Content-Type\r\napplication/octet-stream\r\n`，不符合 HTTP 头格式，因此测试中的 `Content-Type:` 字符串匹配失败
- **根因**：`serve_file` 和 `http_response_send` 对 `extra_headers` 数组的数据格式约定不一致——前者按 name/value 对传入，后者按 `"Name: value"` 字符串处理。生成时两个函数之间缺少统一的数据格式约定
- **层级**：**Coder（跨函数数据格式约定不一致）**
- **涉及代码**：`http_server.c:serve_file`、`http_response.c:http_response_send`

### 3.4 SMTP — `smtp_data_delivery`：DATA 正文后期望 250，实际 451

- **现象**：邮件正文发送完毕后期望 250 OK，实际收到 451 Local error in processing
- **直接原因**：`smtp_mail_store_write` 中 `mkstemp` + `rename` 的实现有 bug：
  ```c
  snprintf(temp_name, ..., "%s/XXXXXX.eml", root_dir); // "/tmp/xxx/XXXXXX.eml"
  *strrchr(temp_name, '.') = '\0';   // 去掉 .eml → "/tmp/xxx/XXXXXX"
  int fd = mkstemp(temp_name);        // mkstemp 替换 XXXXXX → "/tmp/xxx/aBcDeF"
                                        // 磁盘文件：/tmp/xxx/aBcDeF（无 .eml）
  strcat(temp_name, ".eml");          // temp_name = "/tmp/xxx/aBcDeF.eml"
  if (rename(temp_name, temp_name))   // BUG! 源=目标="/tmp/xxx/aBcDeF.eml"
      return -1;                      // 但磁盘上的文件是 /tmp/xxx/aBcDeF
  ```
  `rename` 的两个参数指向同一个字符串，但磁盘上存在的文件是无 `.eml` 后缀的 mkstemp 生成名。尝试将不存在的 `.eml` 文件重命名为自身 → errno=ENOENT → 返回 -1
- **根因**：LLM 生成的代码未正确保存 mkstemp 修改前的文件名（无后缀版本），导致 rename 的源参数指向了错误的目标。正确的实现应该是：`char old[256]; strcpy(old, temp_name); strcat(temp_name, ".eml"); rename(old, temp_name);`
- **层级**：**Coder（文件操作逻辑错误）**
- **涉及代码**：`mail_store.c:smtp_mail_store_write`

### 3.5 SMTP — `smtp_unknown_command`：未知命令后 QUIT 返回 -1

- **现象**：发送 GARBAGE（无法识别的命令），期望 500/502，然后 QUIT 期望 221。实际 QUIT 返回 -1（连接断开）
- **直接原因**：`handle_command` 中 QUIT 分支调用 `close_control(session)` 直接关闭 socket fd，但此前通过 `queue_code` 排队的 502 和 221 响应仍留在输出缓冲区中，尚未 flush 到 TCP socket。流程：
  ```
  GARBAGE → queue_code(502)  → 缓冲 502，注册 EPOLLOUT
  QUIT    → queue_code(221)  → 缓冲 221
          → close_control()  → epoll_ctl(DEL) + close(fd)  ← fd 已销毁
  on_event_cb 末尾: flush_control_now → send() on closed fd → 失败
  ```
- **根因**：`handle_command` 中 QUIT 处理缺少"先 flush 排队数据再关闭连接"的逻辑。`queue_code` 只是将数据写入内存缓冲区并通过 `update_interest` 注册 EPOLLOUT 事件，实际发送在后续的 `flush_control_now` 中完成。`close_control` 在 flush 之前销毁了 socket，数据永久丢失
- **层级**：**Coder（资源生命周期管理）**
- **涉及代码**：`smtp_server.c:handle_command`、`smtp_server.c:close_control`

### 3.6 SMTP — `smtp_smoke_test`：完整 SMTP 事务连接被重置

- **现象**：执行完整的 HELO→AUTH→MAIL→RCPT→DATA→QUIT 事务时，连接被重置（Connection reset by peer）
- **直接原因**：由 #3.4 和 #3.5 连锁导致。事务进行到 DATA 阶段触发 #3.4 的 mkstemp bug → 返回 451；或在 QUIT 阶段触发 #3.5 的提前关闭 → 连接异常断开
- **根因**：与 #3.4、#3.5 相同
- **层级**：**Coder（#3.4 + #3.5 的连锁效应）**

---

## 四、问题分类汇总

### 按问题层级

| 层级 | 数量 | 问题 |
|------|------|------|
| **测试脚本** | 1 | CoAP stale RST 未排空 |
| **Coder（跨函数语义不一致）** | 6 | HTTP 404→403、HTTP DELETE→400、HTTP Content-Type、SMTP 220 超时、SMTP 403→200 |
| **Coder（函数内部逻辑错误）** | 3 | SMTP mkstemp/rename、SMTP QUIT 提前关闭、HTTP serve_file 宏误用 |

### 按问题类别

| 类别 | 数量 | 典型表现 |
|------|------|----------|
| 返回值语义不匹配 | 3 | SMTP queue_code、HTTP serve_file/S_ISREG、SMTP connection_read |
| 数据格式约定不一致 | 1 | HTTP extra_headers name/value vs "Name: value" |
| 函数职责边界不当 | 1 | HTTP http_resolve_path 安全校验与存在性耦合 |
| 错误分类逻辑 | 1 | HTTP UNKNOWN_METHOD → 400 而非 501 |
| 资源生命周期管理 | 1 | SMTP close_control 在 flush 前关闭 fd |
| 文件操作逻辑 | 1 | SMTP mkstemp/rename 参数错误 |
| 测试代码 | 1 | CoAP UDP socket 未排空 |

---

## 五、系统性分析

### 5.1 跨函数语义不一致是最高频的缺陷模式

在 9 个已识别的问题中，有 6 个（67%）属于"两个或多个函数之间对数据/返回值的语义约定不一致"。典型模式：

- 函数 A 返回正数表示成功（如字节数），函数 B 期望 0 表示成功
- 函数 A 返回抽象类型码（0=文件），函数 B 期望原始系统结构体（mode_t）
- 函数 A 传递 name/value 分离的数组，函数 B 期望已拼接的 "Name: value" 字符串

这类问题的根源在于：**LLM 分别独立生成每个源文件，prompt 中只列出被调用函数名，不包含被调用函数的返回值语义/数据格式约定**。

### 5.2 已实施的 Prompt 增强效果

通过 `_callee_return_info()` 在 prompt 中注入被调用函数的 ROLE、ACTION、TEST_VECTORS 信息：

- ✅ 修复了 SMTP `queue_code` 对 `smtp_response_format` 返回值的误判
- ✅ 修复了 HTTP `serve_file` 对 `http_stat_path` 返回值的宏误用
- ⚠️ 但由于 LLM 生成的固有非确定性，每次生成仍可能引入新的跨函数交互错误

### 5.3 剩余问题的修复方向

| 问题 | 可能的修复方向 |
|------|---------------|
| HTTP 404→403 | 拆分 `http_resolve_path` 为安全校验 + 路径构建两步，或改用 `realpath` 前先做存在性检查 |
| HTTP DELETE→400 | `http_request_parse` 中区分语法错误和未知方法，未知方法不应返回 -1 |
| HTTP Content-Type | 统一 `extra_headers` 格式为 `"Name: value"` 字符串，或修改 `http_response_send` 支持 name/value 对 |
| SMTP mkstemp | 正确保存 mkstemp 前的文件名用于 rename |
| SMTP QUIT 关闭 | QUIT 分支中先调用 flush 再 close_control |
