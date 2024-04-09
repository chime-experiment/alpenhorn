#!/usr/bin/bash

# alpenhorn start-up script for robot node at niagara.

# This script is meant to be executed by via a command= directive
# in chimedat's authorized key list

# To interact with this script, specify a command on the inbound 
# ssh command-line when using the appropriate key:
#
# ssh robot.niagara.alliancecan.ca -i <chimedat-robot-keyfile> \
#    { start | stop | restart }
#
#
# Available commands:
#
#  start    start alpenhornd in a screen if not already running
#  stop     stop alpenhornd (and the screen) if running
#  restart  forced-restart: equivalent to "stop" and then "start"

THIS_SCRIPT=$(basename $0)
SCREEN=/usr/bin/screen

# NB: The inbound command ends up in $SSH_ORIGINAL_COMMAND

# Run screen(1) with logging.  Parameters are passed to screen(1)
function run_screen() {
  echo "running: screen $@"
  logger -t automation -p local0.info "Command called by $THIS_SCRIPT for user $USER: $SCREEN $*"
  $SCREEN "$@"
}

# Part one: vet the inbound command
if [ "x$SSH_ORIGINAL_COMMAND" != "xstart" \
  -a "x$SSH_ORIGINAL_COMMAND" != "xstop" \
  -a "x$SSH_ORIGINAL_COMMAND" != "xrestart" \
  ]
then
  # Reject all unsupported input
  logger -t automation -p local0.info "Command rejected by $THIS_SCRIPT for user $USER: $SSH_ORIGINAL_COMMAND"
  exit 1
fi



# Part two: if asked to stop or restart, stop an existing daemon
if [ "$SSH_ORIGINAL_COMMAND" = "stop" \
  -o "$SSH_ORIGINAL_COMMAND" = "restart" \
  ]
then
  echo "$0: Killing alpenhornd (if running)"
  run_screen -S alpenhornd -X quit
  if [ "$SSH_ORIGINAL_COMMAND" = "restart" ]
  then
    # if force-restarting, wait for termination
    sleep 5
  fi
fi


# Part three: if asked to start or restart, start daemon if necessary
if [ "$SSH_ORIGINAL_COMMAND" = "start" \
  -o "$SSH_ORIGINAL_COMMAND" = "restart" \
  ]
then
  # this tests whether a screen called "alpenhornd" is running
  if ! run_screen -S alpenhornd -Q select . &>/dev/null
  then
    # spawn a new detached screen using the alpenstart inner
    # script.  sg ensures we're using the correct group
    echo "$0: starting alpenhornd in a screen"
    run_screen -d -m -S alpenhornd /usr/bin/sg chime ${HOME}/bin/alpenstart
  else
    echo "$0: alpenhornd already running"
  fi
fi
