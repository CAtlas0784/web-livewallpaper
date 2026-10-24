FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV LOOP_DATA_DIR=/data
RUN useradd --create-home --uid 10001 loop && mkdir -p /data && chown -R loop:loop /app /data
USER loop
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
