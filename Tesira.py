#!/usr/bin/env python3
from utils.TesiraConnectionHandle import *
from threading import Thread, Event
import sys, re, pprint, json, pathlib, logging

class Tesira:
    """
    Representative "object" for a Biamp Tesira DSP. Automatically handles configuration query,
    state updates, and such in the background
    """

    def __init__(self, connection : TesiraConnectionHandle, dspAttributesFile : str = None):
        """
        Initializer function

        This is a bit big - it first starts the communication channel,
        wait for that channel to be active, then query the device for many many
        things, from device ID to ALL DSP blocks

        After this is done, it'll call another function to set up subscription
        to keep us updated on the current state of things
        """
        # Logger
        self.logger = logging.getLogger(__name__)

        # Exit event
        self.__exit = Event()

        # Backend connection (calling this will also start it)
        self.__connection = connection

        # Wait for init to complete
        self.logger.debug("waiting for connection")
        while not self.__connection.active:
            time.sleep(0.1)
        self.logger.debug("device connected")

        # Query hostname
        _, _, self.__hostname = self.__parseResponse(self.__connection.send_wait("DEVICE get hostname"))
        self.logger.info(f"device hostname: {self.__hostname}")

        # Query version
        _, _, self.__version = self.__parseResponse(self.__connection.send_wait("DEVICE get version"))
        self.logger.info(f"device firmware version: {self.__version}")

        # Query DSP block IDs/names
        _, _, self.__dspAliases = self.__parseResponse(self.__connection.send_wait("SESSION get aliases"))
        self.logger.debug(f"found {len(self.__dspAliases)} attribute aliases")

        # This step will take a long time - we query ALL DSP blocks and their attributes. To save time,
        # this can be optionally cached in cases where the configuration is expected to be static
        self.__dspBlocks = {}

        # If cached attributes file is specified:
        cacheLoadSuccess = False
        if dspAttributesFile is not None:
            try:
                self.logger.debug(f"trying DSP attributes file: {dspAttributesFile}")

                with open(dspAttributesFile, "r") as f:
                    dspAF = json.load(f)

                    # Make sure hostname and firmware version matches
                    assert dspAF["hostname"] == self.__hostname, "hostname mismatch"
                    assert dspAF["firmware"] == self.__version, "firmware version mismatch"
                    assert dspAF["nAliases"] == len(self.__dspAliases), "alias count mismatch"

                    self.__dspBlocks = dspAF["blocks"]
                    self.logger.info("DSP attributes loaded from cache file")
                    cacheLoadSuccess = True

            except Exception as e:
                self.logger.error(f"cached DSP attribute file load exception: {e}")

        if dspAttributesFile is None or (not cacheLoadSuccess):
            self.logger.info("DSP attributes will be queried from device (this may take a while)")

            # Traverse all DSP blocks and discover types
            for i, blockID in enumerate(self.__dspAliases):

                # Intentionally send an invalid command to get interface info
                _, _, resp =  self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get BLOCKTYPE"))
                resp = resp.split(" ")[-1].strip()

                if "::Attributes" not in resp:
                    # Not a DSP block (probably device?) - SKIP
                    continue

                # Figure out block interface type
                blockType = str(resp).replace("Interface::Attributes", "").strip()
                self.__dspBlocks[blockID] = {
                    "supported" : False,        # initially everything is unsupported, will be set by attribute discovery later
                    "type" : str(blockType)         # hey, this is important!
                }
                self.logger.debug(f"(DSP block discovery: {i + 1}/{len(self.__dspAliases)}) {blockID} -> {blockType}")

            # Now, for supported types, discover their attributes
            for i, blockID in enumerate(self.__dspBlocks.keys()):
                blockType = self.__dspBlocks[blockID]["type"]
                self.logger.debug(f"(DSP block attribute query: {i + 1}/{len(self.__dspBlocks)}) {blockID} -> {blockType}")
                
                # Level and mute control blocks
                if blockType in ["LevelControl", "MuteControl"]:

                    # Definitely supported
                    self.__dspBlocks[blockID]["supported"] = True

                    # Ganged controls?
                    _, _, self.__dspBlocks[blockID]["ganged"] = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get ganged"))
                    self.__dspBlocks[blockID]["ganged"] = bool("true" in self.__dspBlocks[blockID]["ganged"])

                    # Channel info
                    _, _, chanCount = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get numChannels"))
                    chanCount = int(chanCount)
                    channels = {}
                    for i in range(1, chanCount + 1):
                        _, _, chanLabel = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get label {i}"))
                        channels[i] = {
                            "idx" : i,
                            "label" : chanLabel,
                            "muted" : False,
                        }

                        # If level control, add level channel too
                        if blockType == "LevelControl":
                            channels[i]["level"] = -100.0

                    self.__dspBlocks[blockID]["channels"] = channels

            # Save DSP block information in the cache directory
            pathlib.Path(".cache").mkdir(parents = True, exist_ok = True)
            with open(f".cache/{self.__hostname}.cdspblk", "w") as f:
                json.dump({
                    "blocks" : self.__dspBlocks,
                    "hostname" : self.__hostname,
                    "firmware" : self.__version,
                    "nAliases" : len(self.__dspAliases)
                }, f, indent = 4)
            self.logger.debug(f"DSP attributes saved: {self.__hostname}.cdspblk")

            # Done!
            self.logger.info("DSP attributes loaded from device")

        # Now we transition into asynchronous (ish) mode, starting the receiver callback 
        # that constantly reads and parses incoming data from the DSP
        self.__readThread = Thread(target = self.__readLoop)
        self.__readThread.daemon = True
        self.__readThread.start()

        # Now we can start subscription to get notified whenever something changes
        self.logger.debug("starting subscriptions")
        for blockID, blockAttribute in self.__dspBlocks.items():

            # Subscribe to current level for levelControl
            if blockAttribute["type"] == "LevelControl":
                self.__connection.send(self.__getSubscribeCommand(blockID, "levels"))
                self.__connection.send(self.__getSubscribeCommand(blockID, "mutes"))
                self.logger.debug(f"level subscription: {blockID}")
            
            # Subscribe to mute states for muteControl
            elif blockAttribute["type"] == "MuteControl":
                self.__connection.send(self.__getSubscribeCommand(blockID, "mutes"))
                self.logger.debug(f"mute state subscription: {blockID}")   

        # Done!
        return

    def close(self):
        """
        Gracefully stop operations
        """

        # Set exit flag and wait for read thread to terminate
        self.__exit.set()
        self.__readThread.join()

        # Stop backend connection
        self.__connection.close()

        # Done!
        return

    # Supported subscription types and their prefix IDs,
    # such that we can decode them later
    __subscriptionTypeIDs = {
        "levels" : "LVLA",
        "mutes" : "MUTA",
    }

    def __getSubscribeCommand(self, blockID, subscribeType):
        """
        Return subscribe command for specific value types in blockID.
        Also handles prefixing of subscription IDs so the type can
        be parsed later on

        Note: Tesira IDs can only contain letters, numerals, hyphens, 
              underscores, and spaces
        """
        stid = self.__subscriptionTypeIDs[subscribeType]
        return f"\"{blockID}\" subscribe {subscribeType} \"S_{stid}_{blockID}\""

    def __getSubscriptionTypeBySTID(self, stid):
        """
        Given subscription type ID string, get the actual type key (first match)
        """
        for i, v in self.__subscriptionTypeIDs.items():
            if v == stid:
                return i

        raise Exception(f"STID match failed: {stid}")

    def __readLoop(self):
        """
        Read loop / receiving thread
        Gets and processes data returned from the transport channel as they come in
        (may not synchronously match up with commands sent...)
        """
        self.logger.debug("read loop init")
        while not self.__exit.is_set():
            if self.__connection.active and self.__connection.recv_ready:

                # Try to parse incoming data
                try:
                    # Hey, we got something here
                    buf = self.__connection.recv(self.__connection.readBufferSize)
                    parseOK, msgType, msgData = self.__parseResponse(buf)
                    self.logger.debug(f"rx data [{parseOK}][{msgType}]: {msgData}")

                    # Process subscription (update states of DSP blocks)
                    if msgType == "subscription":

                        subscriptionDSPBlockID = msgData["dspBlock"]
                        subscriptionDataType = msgData["type"]
                        subscriptionDataValue = msgData["value"]
                        assert type(subscriptionDataValue) == list, "subscription data value not a list"

                        # Do we have that DSP block?
                        if subscriptionDSPBlockID in self.__dspBlocks:
                            dspBlockAttributes = self.__dspBlocks[subscriptionDSPBlockID]

                            # Now, depending on the block type, this is processed differently
                            # Let's start with level and mute control blocks
                            if dspBlockAttributes["type"] in ["LevelControl", "MuteControl"]:

                                numChannels = len(dspBlockAttributes["channels"])
                                channelIDXs = list(dspBlockAttributes["channels"].keys())

                                assert len(subscriptionDataValue) == numChannels, f"{subscriptionDataType} RX channel value mismatch, got {len(subscriptionDataValue)}, expecting {numChannels}"

                                if subscriptionDataType == "mutes":
                                    for i, muteStatus in enumerate(subscriptionDataValue):
                                        cIDX = channelIDXs[i]
                                        self.__dspBlocks[subscriptionDSPBlockID]["channels"][cIDX]["muted"] = bool(muteStatus)

                                elif subscriptionDataType == "levels":
                                    assert dspBlockAttributes["type"] == "LevelControl", "level RX for a mute block?!?"
                                    for i, levelStatus in enumerate(subscriptionDataValue):
                                        cIDX = channelIDXs[i]
                                        self.__dspBlocks[subscriptionDSPBlockID]["channels"][cIDX]["level"] = float(levelStatus)

                            # Updated
                            self.logger.info(f"attribute updated: {subscriptionDSPBlockID}: {self.__dspBlocks[subscriptionDSPBlockID]}")

                        else:
                            # Huh? This block doesn't exist?!
                            raise Exception(f"subscription RX for invalid block: {subscriptionDSPBlockID}")

                # Hmm something bad happened
                except Exception as e:
                    self.logger.error(f"read loop exception: {e}")

            # Throttle this to 10Hz to reduce CPU consumption
            time.sleep(0.1)

        # Done?
        self.logger.debug("read loop terminated")
        return

    def __parseResponse(self, resp):
        """
        Helper function to parse and extract response from the Tesira Text Protocol
        """
        _validResponsePrefixes = ["+OK", "-ERR"]

        # Embedded inner function to detect a value's type and convert if necessary
        # (i.e., separate floats, strings, and booleans)
        def valFormat(v):
            v = str(v).strip()
            try:
                # First try to return as a float
                return float(v)
            except ValueError:
                # No? Then this is either a bool or string,
                # let's figure out what it is. A bool?
                if v.lower() in ["true", "yes", "on"]:
                    return True
                elif v.lower() in ["false", "no", "off"]:
                    return False
                else:
                    # Nope, this is just a string
                    return v

        line = None
        returnType = None

        for l in resp.splitlines():
            if l.startswith("+OK"):
                line = str(l[3:]).strip()
                returnType = "ok"
                break
            elif l.startswith("-ERR"):
                line = str(l[4:]).strip()
                returnType = "error"
                break
            elif l.startswith("!"):
                line = str(l[1:]).strip()
                returnType = "subscription"
                break

        # Nothing is parsed
        if line is None:
            return False, None, None

        # We got something, now if it's an "OK" response, what'd we get?
        if returnType == "ok":

            dType = line.split(":", 1)[0].replace('"', '')
            dValue = line.split(":", 1)[1].strip()

            if dType == "value":
                # Straight value type
                return True, returnType, valFormat(str(dValue.replace('"', '')))

            elif dType == "list":
                # List type (needs a bit of parsing)
                items = list(re.findall('"([^"]*)"', dValue.split("[", 1)[1].split("]", 1)[0].strip()))
                return True, returnType, [valFormat(i) for i in items]

            else:
                # What is this?!?
                self.logger.warning(f"unknown OK response data type: {dType} -> {line}")
                return True, returnType, line

        # If we get a subscription response, the return will need a bit more parsing
        # as we also need to handle publishToken and list of values
        elif returnType == "subscription":

            rData = {}
            keyvals = list(re.findall("(\[.*?\]|\".*?\"):(\[.*?\]|\".*?\")", line))

            for item in keyvals:
                assert len(item) == 2, f"Returned item match error: {keyvals} has item with invalid length {len(item)}"
                key = str(item[0]).replace("\"", "").strip()

                # Is return value a list?
                if "[" in str(item[1]):
                    value = list(str(item[1]).replace("\"", "").replace("[", "").replace("]", "").strip().split(" "))
                    value = [valFormat(i) for i in value]
                else:
                    value = valFormat(str(item[1]).replace("\"", ""))

                rData[key] = value

            # publishToken will need a bit more formatting, considering there may be a prefix code
            assert "publishToken" in rData, f"Subscription callback data with no publish token: {line}"

            # PublishToken should have the prefix "S_{4 character code}_{block id}"
            # we extract it from here
            rt = rData["publishToken"]
            assert rt.startswith("S_"), f"Non-prefixed subscription callback: {rt}"
            rt = rt.split("_", 2)
            
            subscriptionTypeID = str(rt[1]).strip()
            dspBlockID = str(rt[2]).strip()
            assert len(subscriptionTypeID) == 4, f"Invalid subscription type ID in callback: {subscriptionTypeID} (for {dspBlockID})"
            subscriptionType = self.__getSubscriptionTypeBySTID(subscriptionTypeID)

            # Parsed data for easy reference
            rData["type"] = subscriptionType
            rData["dspBlock"] = dspBlockID

            return True, returnType, rData

        else:
            # TODO: this needs fleshing out
            return True, returnType, line    