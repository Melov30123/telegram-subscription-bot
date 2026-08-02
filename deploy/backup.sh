#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir="$project_dir/backups"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$backup_dir"
cd "$project_dir"

docker compose exec -T postgres pg_dump \
  --username=subscription_bot \
  --dbname=subscription_bot \
  --format=custom \
  --no-owner \
  --no-privileges > "$backup_dir/subscription_bot-$timestamp.dump"

find "$backup_dir" -type f -name 'subscription_bot-*.dump' -mtime +14 -delete
echo "Backup created: $backup_dir/subscription_bot-$timestamp.dump"
