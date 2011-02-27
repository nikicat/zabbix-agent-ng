#!/bin/bash

cat /proc/slabinfo | grep "$1" | awk '{ print $'$2' }'
