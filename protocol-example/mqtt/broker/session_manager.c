#include "session_manager.h"

#include <stdlib.h>

struct mqtt_session_manager {
    mqtt_session_t** sessions;
    size_t count;
    size_t cap;
};

static void ensure_cap(mqtt_session_manager_t* m, size_t need) {
    if (!m) {
        return;
    }
    if (need <= m->cap) {
        return;
    }
    size_t nc = m->cap ? m->cap * 2 : 16;
    while (nc < need) {
        nc *= 2;
    }
    mqtt_session_t** p = (mqtt_session_t**)realloc(m->sessions, nc * sizeof(*p));
    if (!p) {
        return;
    }
    m->sessions = p;
    m->cap = nc;
}

mqtt_session_manager_t* mqtt_session_manager_create(void) {
    mqtt_session_manager_t* m = (mqtt_session_manager_t*)calloc(1, sizeof(*m));
    return m;
}

void mqtt_session_manager_destroy(mqtt_session_manager_t* m) {
    if (!m) {
        return;
    }
    for (size_t i = 0; i < m->count; ++i) {
        mqtt_session_destroy(m->sessions[i]);
    }
    free(m->sessions);
    free(m);
}

mqtt_session_t* mqtt_session_manager_get(mqtt_session_manager_t* m, int session_id) {
    if (!m) {
        return NULL;
    }
    for (size_t i = 0; i < m->count; ++i) {
        if (mqtt_session_id(m->sessions[i]) == session_id) {
            return m->sessions[i];
        }
    }
    return NULL;
}

void mqtt_session_manager_add(mqtt_session_manager_t* m, mqtt_session_t* s) {
    if (!m || !s) {
        return;
    }
    const int sid = mqtt_session_id(s);

    // replace if exists
    for (size_t i = 0; i < m->count; ++i) {
        if (mqtt_session_id(m->sessions[i]) == sid) {
            mqtt_session_destroy(m->sessions[i]);
            m->sessions[i] = s;
            return;
        }
    }

    ensure_cap(m, m->count + 1);
    if (m->count < m->cap) {
        m->sessions[m->count++] = s;
    } else {
        // failed to grow
        mqtt_session_destroy(s);
    }
}

void mqtt_session_manager_remove(mqtt_session_manager_t* m, int session_id) {
    if (!m) {
        return;
    }
    for (size_t i = 0; i < m->count; ++i) {
        if (mqtt_session_id(m->sessions[i]) == session_id) {
            mqtt_session_destroy(m->sessions[i]);
            m->sessions[i] = m->sessions[m->count - 1];
            m->count--;
            return;
        }
    }
}
