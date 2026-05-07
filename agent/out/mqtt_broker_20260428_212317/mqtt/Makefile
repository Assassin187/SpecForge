CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

BROKER_SRCS = \
	network/connection.c \
	network/tcp_server.c \
	protocol/mqtt_packet.c \
	protocol/mqtt_decoder.c \
	protocol/mqtt_encoder.c \
	broker/session.c \
	broker/session_manager.c \
	topic/topic_tree.c \
	router/message_router.c \
	main.c \
	broker/broker.c

TARGET ?= mqtt_broker

all: $(TARGET)

$(TARGET): $(BROKER_SRCS)
	$(CC) $(CFLAGS) -o $@ $(BROKER_SRCS) $(LDFLAGS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
