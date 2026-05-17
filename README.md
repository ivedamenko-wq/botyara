# Debt Tracker Bot

Telegram-бот для отслеживания долгов между двумя пользователями. Работает через инлайн-кнопки, хранит историю с хэш-цепочкой целостности.

---

## Возможности

- Добавить долг (я должен / мне должны)
- Баланс с автоматической компенсацией
- История операций с удалением
- Взаиморасчёт одной кнопкой
- Псевдонимы для отображения имён
- Проверка целостности истории (SHA-256 хэш-цепочка)
- Белый список из двух пользователей

---

## Структура проекта

```
botyara/
├── bot.py              # хендлеры Telegram
├── db.py               # база данных SQLite + хэш-цепочка
├── config.py           # токен и белый список (не коммитить!)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .github/
    └── workflows/
        └── deploy.yml  # CI/CD через GitHub Actions
```

---

## Развёртывание на Ubuntu-сервере

### Шаг 1 — Создай Telegram-бота

1. Открой [@BotFather](https://t.me/BotFather)
2. Отправь `/newbot`, следуй инструкциям
3. Скопируй выданный токен вида `1234567890:AAF...`

---

### Шаг 2 — Подготовь сервер

Подключись по SSH и выполни:

```bash
# Обнови пакеты и установи зависимости
sudo apt update && sudo apt install -y git

# Установи Docker
curl -fsSL https://get.docker.com | sh

# Создай пользователя для бота
sudo useradd -m -s /bin/bash botuser
sudo usermod -aG docker botuser

# Переключись на нового пользователя
sudo su - botuser
```

---

### Шаг 3 — Залей код на сервер

На своей машине создай репозиторий на GitHub и запушь код:

```bash
git init
git remote add origin https://github.com/ВАШ_НИК/botyara.git

# Добавь config.py в .gitignore — токен не должен попасть в репозиторий
echo "config.py" >> .gitignore
echo "*.db" >> .gitignore

git add .
git commit -m "init"
git push -u origin main
```

На сервере (от имени botuser):

```bash
git clone https://github.com/ВАШ_НИК/botyara.git
cd botyara
```

---

### Шаг 4 — Создай config.py на сервере

`config.py` не хранится в репозитории — создай его вручную на сервере:

```bash
nano ~/botyara/config.py
```

Содержимое:

```python
BOT_TOKEN = "1234567890:AAF..."   # токен от BotFather

ALLOWED_USERS = {
    "your_username",      # твой username без @
    "friend_username",    # username друга без @
}
```

Сохрани: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

### Шаг 5 — Запусти бота через Docker

```bash
cd ~/botyara
docker compose up -d
```

Docker соберёт образ и запустит контейнер. База данных хранится в именованном volume `db_data` — она переживает пересборку образа.

Проверь что бот работает:

```bash
docker compose logs -f
```

Должны появиться строки:
```
[INFO] __main__: Бот запущен. Разрешённые пользователи: {'your_username', 'friend_username'}
```

---

### Шаг 6 — Настрой автодеплой через GitHub Actions

**Создай SSH-ключ на сервере** (от имени botuser):

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Выведи приватный ключ — он понадобится на следующем шаге:

```bash
cat ~/.ssh/github_deploy
```

**Добавь секреты в GitHub:**

Открой репозиторий → Settings → Secrets and variables → Actions → New repository secret:

| Название | Значение |
|----------|---------|
| `SSH_PRIVATE_KEY` | содержимое файла `~/.ssh/github_deploy` (приватный ключ, многострочный) |
| `SSH_HOST` | IP-адрес сервера |
| `SSH_USER` | `botuser` |

**Разреши botuser перезапускать контейнер без пароля:**

```bash
# От имени root
sudo visudo
```

Добавь строку в конец файла:

```
botuser ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker compose
```

Файл `.github/workflows/deploy.yml` уже есть в репозитории. Теперь при каждом `git push` в ветку `main` GitHub Actions:

1. Подключится к серверу по SSH
2. Выполнит `git pull`
3. Пересоберёт Docker-образ
4. Перезапустит контейнер

---

## Полезные команды на сервере

```bash
# Логи в реальном времени
docker compose logs -f

# Перезапустить бота
docker compose restart bot

# Остановить
docker compose down

# Пересобрать и запустить после ручных изменений
docker compose up -d --build

# Посмотреть статус контейнера
docker compose ps

# Посмотреть volume с базой данных
docker volume inspect botyara_db_data
```

---

## Как работает компенсация долгов

Баланс считается как чистая сумма всех транзакций:

```
Друг заплатил 500р  →  ты должен 500р
Ты заплатил 600р    →  теперь друг должен 100р
```

Кнопка «Взаиморасчёт» записывает компенсирующую транзакцию и обнуляет баланс.

---

## Целостность истории

Каждая запись в базе содержит SHA-256 хэш от своих данных и хэша предыдущей записи (хэш-цепочка). Любое изменение данных в базе напрямую будет обнаружено кнопкой «Проверка».
