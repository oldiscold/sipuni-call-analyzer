FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости отдельно для кэширования слоя
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Создаём директорию для временных файлов
RUN mkdir -p /tmp/calls

EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port $PORT
