[PROMPT]
Implement function `smtp_auth_decode_base64`. Responsibility: 忽略空白并解码 base64 文本到 out 缓冲

[RELY]
- FUNC `b64_value`
  role: 被该函数调用以完成子步骤
```c
static int b64_value(char c);
```

[GUARANTEE]
```c
int smtp_auth_decode_base64(const char* input, unsigned char* out, size_t out_cap, size_t* out_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：input(const char*，可为 NULL，BORROWED)；out(unsigned char*，可为 NULL，BORROWED)；out_cap(size_t，不可为 NULL，BORROWED)；out_len(size_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0，把解码后的准确字节数写入 out_len，并仅写入该长度范围内的 out；输入/输出指针无效、有效字符不能组成完整 quartet、padding 位置非法、padding 后存在非空白字符、Base64 字符非法或 out_cap 不足时返回 -1。padding quartet 处理完成后不得 break 后保留 quartet_pos==4 再由 post-loop 重复处理。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 遵循 AUTH.base64_payload 映射规则：非法字符返回 -1，padding '=' 只能出现在最后一个完整 quartet，输出长度写入 out_len
- quartet 位置只由已接受的非 whitespace 字符数量决定，与 input 的内存地址和原始索引无关
- 一个 '=' 产生两个输出字节，两个 '==' 产生一个输出字节，padding 只能出现在最后一个完整 quartet
- 任何输出写入前都必须证明剩余 out_cap 足够
- 每个完整 quartet 处理后必须清零 quartet_pos；padding quartet 处理后进入 final_quartet_seen，后续只允许 whitespace
- 循环结束时 quartet_pos 必须为 0；不得 post-loop 解码残余 2 或 3 个有效字符

**System Algorithm**:
- input、out 或 out_len 为 NULL 时返回 -1。扫描 input 时忽略 ASCII whitespace，并使用 quartet[4]、quartet_pos 和 final_quartet_seen 状态机：每接受一个非 whitespace 字符后放入 quartet[quartet_pos++]，每个完整四字符 quartet 必须立即处理；处理后 quartet_pos 必须清零，若该 quartet 含 padding 则设置 final_quartet_seen。final_quartet_seen 后只允许继续扫描 ASCII whitespace，任何非 whitespace 字符返回 -1。不得使用 input 指针地址、pointer cast 或包含 whitespace 的原始字符串索引决定 quartet 边界。计算输出字节前必须先判断 padding 位置，'=' 不得参与 bit shift。第 1 或第 2 位出现 '=' 非法；第 3 位为 '=' 时第 4 位也必须为 '='，输出 1 字节；第 4 位单独为 '=' 输出 2 字节；无 padding 输出 3 字节。循环结束时若 quartet_pos != 0 返回 -1，不得在循环后尝试解码 2 或 3 个残余字符。带单个 padding 的合法 payload c210cHVzZXI= 和 c210cHBhc3M= 必须分别成功解码为 smtpuser 和 smtppass。每次写出前检查 out_cap，任一非法字符、非法长度、非法 padding 或容量不足均返回 -1。
