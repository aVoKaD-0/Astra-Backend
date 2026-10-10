# Astra

Загрузка CSV в PostgreSQL и API на FastAPI для записи, фильтрации и агрегации продаж.

## Структура

- `core/data/` — работа с БД.
- `core/api/` — работа с API.
- `core/records.py` — проверка данных.
- `core/import_csv.py` — загрузка CSV.
- `core/main.py` — приложение.
- `core/config.py` — настройки.
- `tests/` — тесты.

## Запуск

Нужны Python 3.9+ и Docker Compose, команды выполняются из корня проекта.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Если `.env` ещё нет, создайте его из шаблона:

```bash
cp .env.example .env
```

Заполните `POSTGRES_USER` и `POSTGRES_PASSWORD`. Для локальной БД оставьте `POSTGRES_HOST=127.0.0.1` и `POSTGRES_PORT=5432`.

Поднимите PostgreSQL внутри Docker:

```bash
docker compose up -d --wait
python -m core.data.database
```

Скачайте [датасет с Kaggle](https://www.kaggle.com/datasets/sergionefedov/us-building-materials-sales-transactions-20192024/data) и распакуйте CSV в `us-building-materials-sales-transactions-20192024/`.

Импортируйте данные из csv и запишите в БД:

```bash
python -m core.import_csv \
  --macro us-building-materials-sales-transactions-20192024/macro_drivers_weekly.csv \
  --transactions us-building-materials-sales-transactions-20192024/building_materials_transactions.csv
```

Команда сначала загружает недели, затем продажи, повторный импорт пропускает
уже известные продажи.
Ограничения описаны в [TASK.MD](TASK.MD#механизм-инкрементальной-загрузки).

## API

Все методы и параметры доступны в [Swagger UI](http://127.0.0.1:8000/docs). Недельные показатели добавляются через `PUT /macro-weeks/{week}`, продажи - через `POST /transactions`. Неделя должна существовать до добавления продажи.

Поднимите API:

```bash
python -m uvicorn core.main:app --reload --host 127.0.0.1 --port 8000
```

Примеры чтения и агрегации:

```bash
curl 'http://127.0.0.1:8000/transactions?region=Pacific&limit=20'
curl 'http://127.0.0.1:8000/transactions/aggregate?region=Pacific&group_by=product_category'
```

## Проверки

Запустите тесты:

```bash
python -m unittest discover -s tests -v
python -m ruff check core tests start.py
python -m ruff format --check core tests start.py
```

Проверка полного датасета и повторного импорта:

```bash
ASTRA_FULL_DATASET_TEST=1 python -m unittest tests.test_dataset -v
```
