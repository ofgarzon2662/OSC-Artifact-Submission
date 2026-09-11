#!/bin/bash
set -e

echo "Starting Enhanced RabbitMQ with Message Monitoring..."

# Start RabbitMQ server in the background
echo "Starting RabbitMQ server..."
rabbitmq-server &
RABBITMQ_PID=$!

# Wait for RabbitMQ to be ready before creating user
echo "Waiting for RabbitMQ to start..."
until rabbitmqctl status > /dev/null 2>&1; do
    sleep 2
done

# Create the user from environment variables
if [ -n "$RABBITMQ_DEFAULT_USER" ] && [ -n "$RABBITMQ_DEFAULT_PASS" ]; then
    echo "Creating user '$RABBITMQ_DEFAULT_USER'..."
    rabbitmqctl add_user "$RABBITMQ_DEFAULT_USER" "$RABBITMQ_DEFAULT_PASS" 2>/dev/null || \
        rabbitmqctl change_password "$RABBITMQ_DEFAULT_USER" "$RABBITMQ_DEFAULT_PASS"
    rabbitmqctl set_user_tags "$RABBITMQ_DEFAULT_USER" administrator
    rabbitmqctl set_permissions -p / "$RABBITMQ_DEFAULT_USER" ".*" ".*" ".*"
    echo "User '$RABBITMQ_DEFAULT_USER' created successfully"
fi

# Run the definitions import in the background
echo "Importing queue definitions..."
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