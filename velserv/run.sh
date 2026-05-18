#!/usr/bin/with-contenv bashio

DEVICE=$(bashio::config 'device')
PORT=$(bashio::config 'port')
VERBOSE_LEVEL=$(bashio::config 'verbose_level')

ARGS="-d ${DEVICE} -p ${PORT} -f"

for i in $(seq 1 "${VERBOSE_LEVEL}"); do
    ARGS="${ARGS} -v"
done

if [ ! -e "${DEVICE}" ]; then
    bashio::log.fatal "Device ${DEVICE} not found. Check the 'device' option and make sure the Velbus USB interface is connected."
    exit 1
fi

bashio::log.info "Starting VelServ on ${DEVICE}, TCP port ${PORT} (verbose level ${VERBOSE_LEVEL})"
bashio::log.info "Command: /usr/local/bin/velserv ${ARGS}"
exec /usr/local/bin/velserv ${ARGS}
