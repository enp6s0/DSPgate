#!/usr/bin/env python3
from Tesira import *
from transports.SSH import *
import time, yaml, logging

# Logger
logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)

# Load configuration
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# Init DSP
try:

    dsp = Tesira(connection = SSH(hostname = config["connection"]["host"],
                                    username = config["connection"]["username"], 
                                    password = config["connection"]["password"]),
                dspAttributesFile = config["dsp"]["attributeCache"] if ("dsp" in config and "attributeCache" in config["dsp"]) else None)

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    dsp.close()