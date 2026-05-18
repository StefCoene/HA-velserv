#!/usr/bin/with-contenv bashio

DEVICE=$(bashio::config 'device')
PORT=$(bashio::config 'port')

ARGS="-d ${DEVICE} -p ${PORT} -f 1"

if bashio::config.true 'server_only'; then
    ARGS="${ARGS} -s"
fi

if bashio::config.true 'verbose'; then
    ARGS="${ARGS} -v"
fi

if ! bashio::config.true 'server_only' && [ ! -e "${DEVICE}" ]; then
    bashio::log.fatal "Device ${DEVICE} not found. Check the 'device' option and make sure the Velbus USB interface is connected."
    exit 1
fi

bashio::log.info "Starting VelServ on ${DEVICE}, TCP port ${PORT}"
exec /usr/local/bin/velserv ${ARGS}
