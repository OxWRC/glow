# Hourly encrypted backups: pg_dump (matches the GLOW DB, postgres 16; newer
# clients dump the ODK postgres 14 fine) + restic.
FROM postgres:16-alpine
RUN apk add --no-cache restic jq \
 && restic version
COPY glow-backup.sh /usr/local/bin/glow-backup.sh
RUN chmod 0755 /usr/local/bin/glow-backup.sh
ENTRYPOINT []
CMD ["glow-backup.sh", "run-loop"]
