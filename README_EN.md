# Telepiplex feature/115

This is a minimal 115 Telegram Bot branch. It keeps only 115 authorization, magnet offline submission, save-directory selection, and top-level folder renaming.

## Commands

| Command | Purpose |
| --- | --- |
| `/start` | Show help |
| `/auth` | Authorize 115 OpenAPI by QR code |
| `/config` | Configure 115 OpenAPI or access and refresh tokens |
| `/reload` | Reload configuration |
| `/magnet magnet-link` | Submit a magnet link to 115 offline download |
| `/m magnet-link` | Short command for `/magnet` |
| `/q` | Cancel the current conversation |

## Flow

1. Send `/magnet magnet:?xt=urn:btih:...`, or use `/m`.
2. Choose a configured 115 save directory.
3. Enter the desired top-level folder name.
4. The bot submits the offline task to 115.
5. After completion, the bot renames only the top-level folder and leaves internal files untouched.

Send `-` to keep the original 115 folder name. If the target folder already exists, the bot appends ` (2)`, ` (3)`, and so on.

## Configuration

Runtime configuration lives at `/config/config.yaml`.

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789

115_app_id: null
access_token: ""
refresh_token: ""

open115:
  timeout: 30

clean_policy:
  switch: "on"
  less_than: 400M

category_folder:
  - name: Movies
    path: /Movies
  - name: Shows
    path: /Shows
```

Use `115_app_id` for QR authorization. For direct-token mode, leave `115_app_id` empty and set `access_token` plus `refresh_token`.

## Docker

```bash
docker build -t telepiplex:feature-115 .
docker run -d \
  --name telepiplex \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -v /path/to/config:/config \
  -v /path/to/tmp:/tmp \
  telepiplex:feature-115
```

## License

This project is derived from `qiqiandfei/Telegram-115bot` and follows the original MIT License. See `LICENSE`.
