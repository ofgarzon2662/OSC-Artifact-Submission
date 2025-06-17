#!/bin/bash

echo "🔄 Starting RabbitMQ definitions import process..."

# Wait for RabbitMQ to be ready
echo "⏳ Waiting for RabbitMQ to be ready..."
RETRY_COUNT=0
MAX_RETRIES=30

until rabbitmqctl status > /dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ RabbitMQ failed to start after $MAX_RETRIES attempts"
        exit 1
    fi
    echo "⏳ RabbitMQ is not ready yet (attempt $RETRY_COUNT/$MAX_RETRIES), waiting..."
    sleep 2
done

echo "✅ RabbitMQ is ready!"

# Enable management plugin explicitly
echo "🔌 Enabling management plugin..."
rabbitmq-plugins enable rabbitmq_management

# Wait a bit more for management plugin to be ready
echo "⏳ Waiting for management plugin to start..."
sleep 10

# Wait for management API to be available (with authentication)
echo "🔍 Checking management API availability..."
MGMT_RETRY=0
MAX_MGMT_RETRIES=15

until curl -s -f -u ${RABBITMQ_DEFAULT_USER}:${RABBITMQ_DEFAULT_PASS} http://localhost:15672/api/overview > /dev/null 2>&1; do
    MGMT_RETRY=$((MGMT_RETRY + 1))
    if [ $MGMT_RETRY -ge $MAX_MGMT_RETRIES ]; then
        echo "❌ Management API failed to become available after $MAX_MGMT_RETRIES attempts"
        echo "🔍 Debug info:"
        echo "   - RabbitMQ status: $(rabbitmqctl status 2>&1 | head -3)"
        echo "   - Active plugins: $(rabbitmq-plugins list --enabled 2>&1)"
        echo "   - Port 15672 listening: $(netstat -tlnp 2>&1 | grep :15672 || echo 'Not listening')"
        exit 1
    fi
    echo "⏳ Management API not ready yet (attempt $MGMT_RETRY/$MAX_MGMT_RETRIES), waiting..."
    sleep 5
done

echo "✅ Management API is ready!"
echo "📋 Importing definitions..."

# Import definitions using management API
RESPONSE=$(curl -s -w "%{http_code}" -u ${RABBITMQ_DEFAULT_USER}:${RABBITMQ_DEFAULT_PASS} \
     -H "Content-Type: application/json" \
     -X POST \
     -d @/etc/rabbitmq/definitions.json \
     http://localhost:15672/api/definitions)

HTTP_CODE="${RESPONSE: -3}"

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "204" ]; then
    echo "✅ Definitions imported successfully! (HTTP $HTTP_CODE)"
    
    # Verify queues were created
    echo "🔍 Verifying queues were created..."
    rabbitmqctl list_queues name
    
else
    echo "❌ Failed to import definitions. HTTP Code: $HTTP_CODE"
    echo "Response: $RESPONSE"
    exit 1
fi

echo "🎉 RabbitMQ setup completed successfully!"