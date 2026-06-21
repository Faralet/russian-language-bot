# Локальный запуск без Docker

Этот путь нужен только если не хотите использовать Docker.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

В `.env` нужно указать локальную БД PostgreSQL:

```env
DATABASE_URL=postgresql+asyncpg://russian_bot:CHANGE_ME_SET_IN_ENV@localhost:5432/russian_bot
```

Запуск:

```bash
python -m app.main
```
