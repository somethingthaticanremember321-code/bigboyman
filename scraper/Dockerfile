FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8001
CMD ["python", "app.py"]
