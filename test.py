#!/usr/bin/env python3
from Tesira import *
from transports.SSH import *
import time, yaml

# Load configuration
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# Init DSP
dsp = Tesira(connection = SSH(hostname = config["connection"]["host"],
                                username = config["connection"]["username"], 
                                password = config["connection"]["password"], 
                                debug = True),
            debug = True)

while True:
    time.sleep(1)