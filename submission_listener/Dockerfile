FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

WORKDIR /app

# Install dependencies
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock

# Create directory for the schema
RUN mkdir -p /app/schema

# Copy the schema file (now from local contracts folder)
COPY contracts/artifact.submitted.v1.schema.json /app/schema/
COPY contracts/artifact.updated.v1.schema.json /app/schema/

# Copy application code
COPY app.py .

USER 10001:10001
EXPOSE 8000
CMD ["python", "app.py"]
