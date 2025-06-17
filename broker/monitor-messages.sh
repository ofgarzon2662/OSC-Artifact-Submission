#!/bin/bash

# Message monitoring script for RabbitMQ
# This runs alongside RabbitMQ to provide detailed message logging

echo "🔍 Starting RabbitMQ Message Monitor..."

# Function to log with timestamp
log_with_timestamp() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Wait for RabbitMQ to be ready
wait_for_rabbitmq() {
    log_with_timestamp "⏳ Waiting for RabbitMQ to start..."
    while ! rabbitmqctl status &>/dev/null; do
        sleep 1
    done
    log_with_timestamp "✅ RabbitMQ is ready!"
}

# Monitor queue statistics
monitor_queues() {
    while true; do
        sleep 5
        
        # Get queue info in simple format (avoid jq dependency issues)
        QUEUE_LIST=$(rabbitmqctl list_queues name messages consumers 2>/dev/null || echo "")
        
        if [ -n "$QUEUE_LIST" ]; then
            echo "$QUEUE_LIST" | while read line; do
                if [[ $line == *"artifact"* ]] && [[ $line != "name"* ]]; then
                    log_with_timestamp "📊 $line"
                fi
            done
        fi
        
        # Log connections
        CONNECTIONS=$(rabbitmqctl list_connections name 2>/dev/null | wc -l)
        if [ "$CONNECTIONS" -gt 1 ]; then
            log_with_timestamp "🔗 Active connections: $((CONNECTIONS-1))"
        fi
    done
}

# Main monitoring function
main() {
    wait_for_rabbitmq
    
    log_with_timestamp "🚀 Starting queue monitoring..."
    
    # Log initial queue status
    log_with_timestamp "📋 Initial queue status:"
    rabbitmqctl list_queues name messages consumers 2>/dev/null | while read line; do
        if [[ $line == *"artifact"* ]]; then
            log_with_timestamp "  $line"
        fi
    done
    
    monitor_queues
}

# Run monitoring
main 