#ifndef SMTP_SERVER_H
#define SMTP_SERVER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct smtp_server smtp_server_t;

smtp_server_t* smtp_server_create(uint16_t port, const char* mail_root);
int smtp_server_start(smtp_server_t* server);
int smtp_server_run(smtp_server_t* server);
void smtp_server_stop(smtp_server_t* server);
void smtp_server_destroy(smtp_server_t* server);

#ifdef __cplusplus
}
#endif

#endif
