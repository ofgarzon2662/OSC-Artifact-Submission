FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY peer_client.py .

# Expose port for health checks
EXPOSE 8000

CMD ["python", "app.py"] 