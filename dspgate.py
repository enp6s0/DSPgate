#!/usr/bin/env python3
#
#    ___  _______            __     
#   / _ \/ __/ _ \___ ____ _/ /____ 
#  / // /\ \/ ___/ _ `/ _ `/ __/ -_)
# /____/___/_/   \_, /\_,_/\__/\__/ 
#               /___/               
#
# DSPgate - REST API gateway for Biamp Tesira DSPs
#
from dsp.Tesira import *
from transports.SSH import *
import time, os, yaml, logging
from flask import Flask, request, jsonify
from functools import wraps

# Version
DSPGATE_VERSION = "0.1.0-dev"

# Logging configuration
logging.basicConfig()
logging.getLogger().setLevel(logging.INFO)

# Main logger
logger = logging.getLogger("DSPgate")

# Load configuration
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# Start Flask app
app = Flask(__name__)

# DSP attributes cache?
# (useful for development or in cases where the DSP configuration is fixed, this reduces
# initialization time, especially in complex setups, by bypassing DSP graph query)
#
# Cache files are generated every time the device is queried, in `.cache/`
#
dspAttributesCache = None
try:
    dspAttributesCache = os.path.realpath(config["dsp"]["attributeCache"])
    assert os.path.isfile(dspAttributesCache), "DSP attribute cache cannot be loaded"
    logger.info(f"using cached DSP attributes: {dspAttributesCache}")
except:
    # Don't care, just query from device (will be slower depending on DSP graph size)
    pass

# Init DSP
dsp = Tesira(connection = SSH(hostname = config["connection"]["host"],
                                    username = config["connection"]["username"], 
                                    password = config["connection"]["password"]),
             dspAttributesFile = dspAttributesCache)

# API landing
@app.route("/")
def landing():
    """
    If the API is called directly at the root level, we return basic stats
    like what API and version this is. We also return whether the backend DSP
    is connected and ready, as well as current server time.
    """
    return jsonify({
        "api" : "DSPgate",
        "version" : DSPGATE_VERSION,
        "ready" : dsp.ready,
        "time" : int(time.time())
    }), 200

def dspMustBeReady(m):
    """
    Wrapper function to ensure that the DSP is ready before
    we perform backend functions
    """
    @wraps(m)
    def dspReadyCheck(*args, **kwargs):
        if not dsp.ready:
            return jsonify({"error": "DSP not ready"}), 500

# Get all blocks
@dspMustBeReady
@app.route("/block", methods = ["GET"])
def getSupportedBlocks():
    """
    Get all supported DSP blocks. Note we hide those that aren't yet supported
    by DSPgate for simplicity
    """
    return jsonify({
        "blocks" : dsp.supportedBlocks
    }), 200

# Get a specific block
@dspMustBeReady
@app.route("/block/<string:blockID>", methods = ["GET"])
def getBlockInfo(blockID: str):
    """
    Get info on a specific block
    """
    block = dsp.block(blockID)
    if not block:
        return jsonify({"error": f"DSP block {blockID} not found"}), 404

    return jsonify(block), 200

# Set attribute of a specific block
@dspMustBeReady
@app.route("/block/<string:blockID>", methods = ["POST", "PATCH"])
def setBlockAttribute(blockID: str):
    """
    Set attribute on a specific block
    """
    reqContent = request.get_json(force = True, silent = False)
    if not reqContent:
        return jsonify({"error": f"Empty request"}), 400

    # Block must exist
    block = dsp.block(blockID)
    if not block:
        return jsonify({"error": f"DSP block {blockID} not found"}), 404

    # What kind of block are we dealing with?
    # Mute / level?
    if block["type"] in ["MuteControl", "LevelControl"]:

        # Channel settings are required
        if "channel" not in reqContent:
            return jsonify({"error": f"Channel must be specified"}), 412

        # Channel must be specified as a key-value dict
        if type(reqContent["channel"]) != dict:
            return jsonify({"error": f"Invalid channel specification type"}), 412

        # For each channel, we process change requests
        for channel, changeRequest in reqContent["channel"].items():

            try:
                channel = int(channel)
            except ValueError:
                return jsonify({"error": f"Value change on {blockID}, non-numeric channel received: {channel}"}), 412

            # Change requests are also expected to be key-value dicts
            if type(changeRequest) != dict:
                return jsonify({"error": f"Invalid change request type on channel {channel}"}), 412

            # For each change request, we have key (what to change) and value (what to set it to)
            for changeKey, changeValue in changeRequest.items():

                # Mute state change?
                if changeKey in ["mute", "muted"]:
                    dsp.setMute(blockID, channel, value = True if str(changeValue).strip().lower() in ["true", "yes", "mute", "muted"] else False)

                elif changeKey in ["level"]:
                    # Level change, only valid on level control blocks
                    if block["type"] in ["LevelControl"]:

                        # We must be able to convert change value into float
                        try:
                            changeValue = float(changeValue)
                        except ValueError:
                            return jsonify({"error": f"Value change on {blockID} channel {channel}, non-numeric value received"}), 412

                        dsp.setLevel(blockID, channel, value = changeValue)

                    else:
                        return jsonify({"error": f"Level adjustment on unsupported block type {block['type']}"}), 412

                else:
                    # Unknown. We log this and just move on
                    logger.warning(f"Unknown change key: {changeKey} (on {blockID})")

        # Once done, we return update OK
        return jsonify({"update": "ok"}), 200

# Let's get the show rolling!
if __name__ == "__main__":
    app.run(debug = True)