#!/bin/sh
set -eu
envsubst < /usr/share/nginx/html/config.json.template > /usr/share/nginx/html/config.json
rm /usr/share/nginx/html/config.json.template
exec nginx -g 'daemon off;'
