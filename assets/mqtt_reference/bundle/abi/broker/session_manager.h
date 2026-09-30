#pragma once

#include "session.h"

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mqtt_session_manager mqtt_session_manager_t;

mqtt_session_manager_t* mqtt_session_manager_create(void);
void mqtt_session_manager_destroy(mqtt_session_manager_t* m);

void mqtt_session_manager_add(mqtt_session_manager_t* m, mqtt_session_t* s);
void mqtt_session_manager_remove(mqtt_session_manager_t* m, int session_id);
mqtt_session_t* mqtt_session_manager_get(mqtt_session_manager_t* m, int session_id);

#ifdef __cplusplus
}
#endif
