#ifndef FTP_RESPONSE_H
#define FTP_RESPONSE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

int ftp_response_format(char* out, size_t out_len, int code, const char* text);

#ifdef __cplusplus
}
#endif

#endif
