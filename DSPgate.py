#!/usr/bin/env python3
from utils.TesiraSSH import *
from flask import Flask, jsonify, request
import yaml

# Load configuration
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# Flask app
app = Flask(__name__)

# Callback function to handle data coming from the DSP
def callback(line):
    print(line)

# Initialize DSP connection handle
dsp = TesiraSSH(hostname = config["connection"]["host"],
                port = int(config["connection"]["port"]),
                username = config["connection"]["username"],
                password = config["connection"]["password"],
                callback = [
                    callback,
                ])

@app.route("/dsp", methods = ["GET"])
def listDspBlocks():
    if not dsp.connected:
        return jsonify({"error": "DSP disconnected"}), 500
    else:
        return jsonify(dsp.blocks)

# Let's get this show going!
if __name__ == "__main__":
    app.run(debug = True)