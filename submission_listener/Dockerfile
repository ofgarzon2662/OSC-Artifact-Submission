FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directory for the schema
RUN mkdir -p /app/schema

# Copy the schema file
COPY ../contracts/artifact.submitted.v1.schema.json /app/schema/

# Copy application code and environment file
COPY app.py .
COPY .env .

CMD ["python", "app.py"] 