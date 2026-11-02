#!/usr/bin/env bash
# Quiesce Ghost, export MySQL and archive content/configuration. Run as root.
# "backup.sh recover" only restarts a Ghost that an interrupted backup stopped.
set -euo pipefail
umask 077
mode=${1:-backup}
[[ $mode == backup || $mode == recover ]] || { echo 'Usage: backup.sh [recover]' >&2; exit 2; }
cd /opt/wtf-adsb
backup_dir=/var/backups/wtf-adsb
install -d -m 0700 "$backup_dir"
exec 9>/run/lock/wtf-adsb-backup.lock
flock -n 9 || { echo 'Another Ghost backup is running.' >&2; exit 1; }
# Holding the lock, any work directory is left from a killed run; it holds a
# plaintext dump and secrets.
find "$backup_dir" -maxdepth 1 -type d -name '.work.*' -exec rm -rf -- {} +
work_dir=$(mktemp -d "$backup_dir/.work.XXXXXX")
# The marker exists while this job has Ghost stopped. It outlives a kill or
# reboot, so a later run knows to start Ghost again.
stopped_marker=.ghost-stopped-by-backup
start_ghost() {
    docker compose up -d --no-deps --wait --wait-timeout 180 ghost &&
        rm -f -- "$stopped_marker" && return
    # Unhealthy but running: Ghost was started, so a later stop is the
    # operator's choice. Not running: keep the marker for recovery.
    if [[ -n $(docker compose ps --status running --status restarting -q ghost) ]]; then
        rm -f -- "$stopped_marker"
    fi
    return 1
}
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -e $stopped_marker ]]; then
        [[ $mode != recover ]] || echo 'Starting Ghost after an interrupted backup.'
        start_ghost || status=1
    fi
    rm -rf -- "$work_dir"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ $mode != recover ]] || exit 0

running_ghost=$(docker compose ps --status running --status restarting -q ghost)
if [[ -n $running_ghost ]]; then
    : >"$stopped_marker"
    docker compose stop --timeout 60 ghost
fi
docker compose exec -T mysql sh -eu -c '
    export MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
    exec mysqldump --user=root --single-transaction --quick --no-tablespaces \
        --set-gtid-purged=OFF --routines --events --triggers --hex-blob ghost
' | gzip >"$work_dir/ghost.sql.gz"
content_dir=$(docker volume inspect --format '{{ .Mountpoint }}' wtf-adsb_ghost_content)
tar -czf "$work_dir/content.tar.gz" -C "$content_dir" .
# Only the dump and the content copy need Ghost stopped.
if [[ -e $stopped_marker ]]; then
    start_ghost
fi
# An empty gzip stream is not an empty file; require mysqldump's final line.
gzip -dc "$work_dir/ghost.sql.gz" | tail -n 1 | grep -q '^-- Dump completed' ||
    { echo 'The MySQL dump is incomplete; no archive was written.' >&2; exit 1; }
tar -czf "$work_dir/configuration.tar.gz" \
    .env compose.yaml Caddyfile tailscale secrets backup.sh wtf-adsb-backup*
printf '%s\n' 'WTF ADS-B backup v1' "Created: $(date -u +%FT%TZ)" \
    'Restore database, content and configuration together. Re-enroll Tailscale.' \
    >"$work_dir/manifest.txt"
(cd "$work_dir" && sha256sum ghost.sql.gz content.tar.gz configuration.tar.gz >SHA256SUMS)
archive="$backup_dir/ghost-$(date -u +%Y%m%dT%H%M%S%NZ).tar"
tar -cf "$work_dir/bundle.tar" -C "$work_dir" \
    ghost.sql.gz content.tar.gz configuration.tar.gz manifest.txt SHA256SUMS
mv "$work_dir/bundle.tar" "$archive"
find "$backup_dir" -maxdepth 1 -type f -name 'ghost-*.tar' -mtime +6 -delete
printf 'Backup complete: %s\n' "$archive"
