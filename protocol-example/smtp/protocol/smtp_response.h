#ifndef SMTP_RESPONSE_H
#define SMTP_RESPONSE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

int smtp_response_format(char* out, size_t out_len, int code, const char* text);
int smtp_response_format_ehlo_caps(char* out, size_t out_len, const char* hostname, size_t max_size);

#ifdef __cplusplus
}
#endif

#endif
