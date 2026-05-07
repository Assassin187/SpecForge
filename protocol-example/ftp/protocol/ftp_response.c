#include "ftp_response.h"

#include <stdio.h>

int ftp_response_format(char* out, size_t out_len, int code, const char* text) {
    if (out == NULL || out_len == 0 || text == NULL) {
        return -1;
    }

    const int n = snprintf(out, out_len, "%d %s\r\n", code, text);
    if (n < 0 || (size_t)n >= out_len) {
        return -1;
    }
    return n;
}
