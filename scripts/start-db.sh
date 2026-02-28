#!/bin/bash

CONTAINER_NAME="team-shared-postgres"
DB_PASSWORD="ragu<pizza" # Change this if you want!
DB_NAME="ragu"
PORT="5432"

echo "Checking database status..."

# Check if the container already exists
if [ "$(docker ps -aq -f name=^/${CONTAINER_NAME}$)" ]; then
    echo "Starting existing PostgreSQL container..."
    docker start ${CONTAINER_NAME}
else
    echo "Creating and starting a new PostgreSQL container..."
    # -p 0.0.0.0:5432:5432 explicitly exposes it to your local network
    # -v creates a volume so your data isn't lost when the container stops
    docker run --name ${CONTAINER_NAME} \
        -e POSTGRES_PASSWORD=${DB_PASSWORD} \
        -e POSTGRES_DB=${DB_NAME} \
        -p 0.0.0.0:${PORT}:5432 \
        -v team-postgres-data:/var/lib/postgresql/data \
        -d postgres:16
fi

echo "===================================================="
echo "✅ PostgreSQL is up and running!"
echo "Database Name: ${DB_NAME}"
echo "User: postgres"
echo "Password: ${DB_PASSWORD}"
echo "===================================================="