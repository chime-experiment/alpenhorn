#!/bin/bash

# Check to see if alpenhornd is running
pgrep -fl alpenhornd > /dev/null

if [ $? -eq 0 ]; then
    echo "Still running."
    exit
fi

echo "Restarting alpenhornd in a screen"

# Kill exisiting alpenhorn session
screen -S alpenhorn_session -X quit

# Create new session
screen -d -m -S alpenhorn_session
screen -S alpenhorn_session -p 0 -X exec bash

# Set up alpenhornd session.
screen -S alpenhorn_session -X screen -t alpenhornd -L alpenhornd

# Kill other screen
#screen -S alpenhorn_session -p 0 -X kill

