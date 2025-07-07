#!/bin/bash
set -e

echo "🚀 Starting Enhanced RabbitMQ with Message Monitoring..."

# Start RabbitMQ in the background
echo "📡 Starting RabbitMQ server..."
rabbitmq-server &
RABBITMQ_PID=$!

# Run the definitions import in the background
echo "📋 Importing queue definitions..."
/usr/local/bin/init-definitions.sh &

# Start message monitoring in the background (with error handling)
echo "🔍 Starting message monitor..."
if [ -f "/usr/local/bin/monitor-messages.sh" ]; then
    /usr/local/bin/monitor-messages.sh &
    echo "✅ Message monitor started successfully"
else
    echo "⚠️ Message monitor script not found, continuing without monitoring"
fi

echo "✅ All services started. RabbitMQ is running..."

# Wait for RabbitMQ process
wait $RABBITMQ_PID 