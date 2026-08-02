# Развёртывание на Timeweb Cloud

## Рекомендуемая конфигурация

- Ubuntu 24.04 LTS;
- 2 vCPU / 4 ГБ RAM / 50 ГБ NVMe (Cloud MSK 50);
- Москва или ближайший к основной аудитории регион;
- отдельный SSH-ключ, вход по паролю после настройки отключить;
- ежедневный внешний бэкап PostgreSQL.

Конфигурации 2 ГБ достаточно для самого бота, но 4 ГБ оставляют безопасный запас для
PostgreSQL, Docker-сборки, обновлений и кратковременного роста рассылок.

## 1. Подготовка сервера

Подключитесь по SSH, обновите систему и установите Docker из официального репозитория
Docker. Не используйте случайные скрипты установки из чатов.

Разрешите SSH в firewall. Публичный HTTP-порт боту не нужен: он использует long polling,
а health endpoint в `docker-compose.yml` привязан к `127.0.0.1`.

## 2. Клонирование

```bash
git clone https://github.com/Melov30123/telegram-subscription-bot.git Telegram-bot
cd Telegram-bot
cp .env.example .env
nano .env
```

Сгенерируйте отдельный пароль БД:

```bash
openssl rand -base64 32
```

Укажите его как `POSTGRES_PASSWORD`. В Docker Compose `DATABASE_URL` для контейнера бота
формируется автоматически и остаётся во внутренней сети Docker.

## 3. Запуск

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 bot
curl http://127.0.0.1:8080/readyz
```

Ожидаемый ответ readiness: `{"status":"ready","database":true}`.

## 4. Автозапуск и обновление

Политика `restart: unless-stopped` автоматически запускает контейнеры после перезагрузки.
Обновление:

```bash
cd Telegram-bot
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 bot
```

Перед крупным обновлением сделайте резервную копию. Миграции обратимо не откатываются
автоматически — это предотвращает случайную потерю новых данных.

## 5. Бэкапы

```bash
chmod +x deploy/backup.sh
./deploy/backup.sh
crontab -e
```

Пример ежедневного запуска в 03:20:

```cron
20 3 * * * cd /opt/Telegram-bot && ./deploy/backup.sh >> /var/log/telegram-bot-backup.log 2>&1
```

Регулярно копируйте `backups/` за пределы VPS и раз в месяц проверяйте восстановление.

## 6. Мониторинг

- настройте внешний HTTP-monitor на `/healthz` через Caddy/Nginx только если он вам нужен;
- отслеживайте `docker compose logs bot`;
- установите уведомления Timeweb по CPU, RAM и диску;
- держите не менее 20% диска свободным;
- проверяйте `/health` и `/stats` в админке.

## Масштабирование

Для 10 000 пользователей один процесс достаточен. Если число одновременных покупок и
рассылок вырастет, сначала увеличьте VPS до 8 ГБ RAM и настройте managed PostgreSQL или
отдельный DB-сервер. Несколько одновременно запущенных polling-копий одного бота не нужны:
Telegram будет конфликтовать при получении обновлений. Горизонтальное масштабирование
следует делать вместе с переходом на webhook и общей очередью задач.
