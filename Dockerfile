FROM python:3.11-slim
WORKDIR /app
COPY requirements .
RUN pip install --no-cache-dir -r requirements
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "python z1_launcher.py && uvicorn z1_bridge:app --host 0.0.0.0 --port 8000"]FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "z1_bridge:app", "--host", "0.0.0.0", "--port", "8000"]
