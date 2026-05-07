CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

BROKER_SRCS = \
	main.c \
	network/connection.c network/tcp_server.c \
	protocol/mqtt_packet.c protocol/mqtt_encoder.c protocol/mqtt_decoder.c \
	broker/session.c broker/session_manager.c broker/broker.c \
	topic/topic_tree.c router/message_router.c

all: mqtt_broker

mqtt_broker: $(BROKER_SRCS)
	$(CC) $(CFLAGS) -o $@ $(BROKER_SRCS) $(LDFLAGS)

clean:
	rm -f mqtt_broker

.PHONY: all clean
