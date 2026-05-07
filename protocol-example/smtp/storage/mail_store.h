#ifndef SMTP_MAIL_STORE_H
#define SMTP_MAIL_STORE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char* helo_name;
    const char* auth_user;
    const char* mail_from;
    const char* const* rcpt_to;
    size_t rcpt_count;
    const char* data;
    size_t data_len;
} smtp_mail_t;

int smtp_mail_store_init(const char* root_dir);
int smtp_mail_store_write(const char* root_dir, const smtp_mail_t* mail, char* out_path, size_t out_path_len);

#ifdef __cplusplus
}
#endif

#endif
