#include "smtp_response.h"

#include <stdio.h>

int smtp_response_format(char* out, size_t out_len, int code, const char* text) {
    if (out == NULL || out_len == 0 || text == NULL) {
        return -1;
    }

    const int n = snprintf(out, out_len, "%d %s\r\n", code, text);
    if (n < 0 || (size_t)n >= out_len) {
        return -1;
    }
    return n;
}

int smtp_response_format_ehlo_caps(char* out, size_t out_len, const char* hostname, size_t max_size) {
    if (out == NULL || out_len == 0 || hostname == NULL) {
        return -1;
    }

    const int n = snprintf(
        out,
        out_len,
        "250-%s\r\n"
        "250-AUTH LOGIN PLAIN\r\n"
        "250-SIZE %zu\r\n"
        "250 8BITMIME\r\n",
        hostname,
        max_size);
    if (n < 0 || (size_t)n >= out_len) {
        return -1;
    }
    return n;
}
