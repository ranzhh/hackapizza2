#!/bin/bash

CONTAINER_NAME="team-shared-postgres"

echo "Stopping PostgreSQL container..."
docker stop ${CONTAINER_NAME}

echo "✅ Database stopped safely."