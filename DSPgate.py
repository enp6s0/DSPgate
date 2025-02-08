#!/usr/bin/env python3
from utils.TesiraSSH import *
import yaml

def gotSomething(line):
    print(line)

if __name__ == "__main__":

    # Load configuration
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    dsp = TesiraSSH(hostname = config["connection"]["host"],
                    port = int(config["connection"]["port"]),
                    username = config["connection"]["username"],
                    password = config["connection"]["password"],
                    setupCommands = [], 
                    callback = [
                        gotSomething,
                    ])

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        dsp.close()