#!/bin/sh
set -eu
# Render on every start, not just the first: the template lives outside the
# web root so it never needs deleting, and a restarted container (docker
# restart, host reboot) can render it again instead of crash-looping.
envsubst < /etc/glow-dashboard/config.json.template > /usr/share/nginx/html/config.json
exec nginx -g 'daemon off;'
