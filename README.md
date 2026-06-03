# Tennis Analyst Bot

Публичный Telegram-бот для анализа теннисных матчей ATP/WTA с расчётом вероятностей и генерацией PDF-отчётов.

## Возможности

- `/analyze` — полный анализ матча с PDF-отчётом
- `/quick` — быстрый текстовый анализ
- Свободный ввод имён игроков
- Факторная модель (рейтинг, грунт, H2H, форма, физика, мотивация)
- Математическая модель Bo3/Bo5 (распределение сетов, тоталы, фора)
- PDF-отчёт с профилями, факторами, сценариями, вердиктом

## Быстрый старт

### 1. Получи ключи

**Telegram Bot Token:**
1. Открой Telegram, найди @BotFather
2. Напиши `/newbot`
3. Задай имя (например: "Tennis Analyst") и username (например: `tennis_analyst_pro_bot`)
4. Скопируй токен

**Anthropic API Key:**
1. Зайди на https://console.anthropic.com
2. Зарегистрируйся
3. Раздел API Keys → Create Key
4. Пополни баланс (минимум $5, хватит на ~50-100 анализов)

### 2. Установи зависимости

```bash
cd tennis-bot
pip install -r requirements.txt
```

### 3. Настрой ключи

```bash
cp .env.example .env
```

Открой `.env` и вставь свои ключи:
```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
ANTHROPIC_API_KEY=sk-ant-...
```

### 4. Запусти

```bash
python bot.py
```

Бот запустится и будет ждать сообщений. Открой его в Telegram и напиши `/start`.

## Деплой на сервер (24/7)

### Вариант A: VPS (DigitalOcean / Hetzner)

```bash
# На сервере:
git clone <your-repo>
cd tennis-bot
pip install -r requirements.txt
cp .env.example .env
nano .env  # вставь ключи

# Запуск через systemd:
sudo nano /etc/systemd/system/tennis-bot.service
```

Содержимое файла:
```ini
[Unit]
Description=Tennis Analyst Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tennis-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
EnvironmentFile=/home/ubuntu/tennis-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable tennis-bot
sudo systemctl start tennis-bot
```

### Вариант B: Railway.app (проще)

1. Залей код на GitHub
2. Зайди на railway.app → New Project → Deploy from GitHub
3. Добавь переменные среды (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY)
4. Deploy!

## Стоимость

- Telegram Bot API — бесплатно
- Claude API: ~$0.05-0.15 за один анализ матча (модель Sonnet)
- VPS: ~$5-10/мес (DigitalOcean, Hetzner)
- Railway: бесплатный tier ~500 часов/мес

## Структура проекта

```
tennis-bot/
├── bot.py              # Главный файл бота (Telegram handlers)
├── analyzer.py         # Claude API + математическая модель
├── pdf_generator.py    # Генерация PDF-отчётов (reportlab)
├── config.py           # Конфигурация и системный промпт
├── requirements.txt    # Зависимости Python
├── .env.example        # Шаблон переменных среды
└── README.md           # Этот файл
```

## Примеры использования

```
/analyze Зверев vs Ходар, Roland Garros QF
/analyze Fonseca vs Mensik, French Open 2026
/quick Andreeva vs Cirstea WTA
/quick Костюк Свитолина
```

Или просто напиши имена: `Fonseca Mensik`
