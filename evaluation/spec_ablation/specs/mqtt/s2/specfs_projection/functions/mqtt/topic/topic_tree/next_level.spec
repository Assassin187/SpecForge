[PROMPT]
Implement function `next_level`. Responsibility: 按 / 分隔提取主题层级分段并推进读取位置

[RELY]
None.

[GUARANTEE]
```c
static bool next_level(const char* s, size_t s_len, size_t* io_pos, const char** out_start, size_t* out_len, bool* out_done);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题字符串 s、长度 s_len、位置指针 io_pos 以及输出片段指针 out_start/out_len/out_done。

**Post-Condition**:
- 参数非法返回 false；成功时输出当前层级片段范围，推进 *io_pos，且 *io_pos 必须始终位于 [0, s_len]，不得推进到 s_len+1 或其他越界哨兵位置。

**Invariant**:
- 不修改输入字符串
- pos < s_len 时必须返回一个真实 segment
- 当前 segment 到字符串末尾时，返回该 segment 且 out_done=true
- pos == s_len 时表示无 segment 可取，out_start=NULL、out_len=0、out_done=true、io_pos 不超过 s_len

**System Algorithm**:
- 按 / 提取下一层片段，输出片段范围并推进游标；out_done 表示本次返回的 segment 是否为字符串最后一层，而不是调用前是否已经结束。
