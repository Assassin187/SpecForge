#include "smtp_auth.h"

#include <ctype.h>
#include <stdint.h>
#include <string.h>

static const char* k_user = "smtpuser";
static const char* k_pass = "smtppass";

const char* smtp_auth_default_user(void) {
    return k_user;
}

int smtp_auth_validate(const char* user, const char* pass) {
    if (user == NULL || pass == NULL) {
        return 0;
    }
    return strcmp(user, k_user) == 0 && strcmp(pass, k_pass) == 0;
}

static int b64_value(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    if (c == '=') return -2;
    return -1;
}

int smtp_auth_decode_base64(const char* input, unsigned char* out, size_t out_cap, size_t* out_len) {
    if (input == NULL || out == NULL || out_len == NULL) {
        return -1;
    }

    uint32_t acc = 0;
    int bits = 0;
    size_t written = 0;

    for (const char* p = input; *p != '\0'; ++p) {
        if (isspace((unsigned char)*p)) {
            continue;
        }

        const int v = b64_value(*p);
        if (v == -1) {
            return -1;
        }
        if (v == -2) {
            break;
        }

        acc = (acc << 6) | (uint32_t)v;
        bits += 6;

        while (bits >= 8) {
            bits -= 8;
            if (written >= out_cap) {
                return -1;
            }
            out[written++] = (unsigned char)((acc >> bits) & 0xffu);
        }
    }

    *out_len = written;
    return 0;
}

int smtp_auth_parse_plain_blob(
    const unsigned char* blob,
    size_t blob_len,
    char* out_user,
    size_t out_user_len,
    char* out_pass,
    size_t out_pass_len) {
    if (blob == NULL || out_user == NULL || out_pass == NULL || out_user_len == 0 || out_pass_len == 0) {
        return -1;
    }

    const unsigned char* first_nul = memchr(blob, '\0', blob_len);
    if (first_nul == NULL) {
        return -1;
    }
    const size_t off1 = (size_t)(first_nul - blob);

    if (off1 + 1 >= blob_len) {
        return -1;
    }

    const unsigned char* second_nul = memchr(blob + off1 + 1, '\0', blob_len - off1 - 1);
    if (second_nul == NULL) {
        return -1;
    }

    const size_t user_off = off1 + 1;
    const size_t user_len = (size_t)(second_nul - (blob + user_off));
    const size_t pass_off = (size_t)(second_nul - blob) + 1;
    const size_t pass_len = blob_len - pass_off;

    if (user_len == 0 || pass_len == 0 || user_len >= out_user_len || pass_len >= out_pass_len) {
        return -1;
    }

    memcpy(out_user, blob + user_off, user_len);
    out_user[user_len] = '\0';

    memcpy(out_pass, blob + pass_off, pass_len);
    out_pass[pass_len] = '\0';
    return 0;
}
