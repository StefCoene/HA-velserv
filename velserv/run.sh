#!/usr/bin/with-contenv bashio

DEVICE=$(bashio::config 'device')
PORT=$(bashio::config 'port')

ARGS="-d ${DEVICE} -p ${PORT} -f"

if bashio::config.true 'verbose'; then
    ARGS="${ARGS} -v"
fi

if [ ! -e "${DEVICE}" ]; then
    bashio::log.fatal "Apparaat ${DEVICE} niet gevonden. Controleer de instelling 'device' en of de Velbus USB interface aangesloten is."
    exit 1
fi

bashio::log.info "VelServ starten op ${DEVICE}, TCP poort ${PORT}"
exec /usr/local/bin/velserv ${ARGS}
