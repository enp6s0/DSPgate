#!/usr/bin/env python3
from utils.TesiraConnectionHandle import *
from threading import Thread, Event
import sys, re, pprint, json, pathlib, logging, traceback

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

        # Ready?
        # (DSP init takes time, so some downstream functions won't be available
        #  until everything is initialized)
        self.__ready = False

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

        # Set verbose output (but nothing too detailed)
        self.__connection.send_wait("SESSION set verbose true")
        self.__connection.send_wait("SESSION set detailedResponse false")

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
                self.logger.warning(f"cannot load cached DSP attributes: {e}")

        # If there's no cached attributes file (or the load failed for any reason
        # we query the device itself to get the latest attributes)
        if dspAttributesFile is None or (not cacheLoadSuccess):
            self.logger.info("DSP attributes will be queried from device (this may take a while)")
            self.__dspBlocks = self.__discoverDSPBlocks(self.__dspAliases, cache = True)
            self.logger.info("DSP attributes loaded from device")

        # Now we transition into asynchronous (ish) mode, starting the receiver callback 
        # that constantly reads and parses incoming data from the DSP
        self.__readThread = Thread(target = self.__readLoop)
        self.__readThread.daemon = True
        self.__readThread.start()

        # Now we can start subscription to get notified whenever something changes
        self.__setupSubscriptions()

        # Done - DSP should now be ready for operations
        self.__ready = True
        return

    def __discoverDSPBlocks(self, aliases : dict, cache : bool = True):
        """
        Discover DSP blocks by querying the DSP. This takes some time, especially
        if the DSP graph is big...
        """

        # This is our return dict
        rtn = {}

        # Traverse all DSP blocks and discover types
        for i, blockID in enumerate(aliases):

            # Intentionally send an invalid command to get interface info
            _, _, resp =  self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get BLOCKTYPE"))
            resp = resp.split(" ")[-1].strip()

            if "::Attributes" not in resp:
                # Not a DSP block (probably the device handle?) - SKIP
                continue

            # Figure out block interface type
            blockType = str(resp).replace("Interface::Attributes", "").strip()
            rtn[blockID] = {
                "supported" : False,        # initially everything is unsupported, will be set by attribute discovery later
                "type" : str(blockType)         # hey, this is important!
            }
            self.logger.debug(f"(DSP block discovery: {i + 1}/{len(aliases)}) {blockID} -> {blockType}")

        # Now, for supported types, discover their attributes
        for i, blockID in enumerate(rtn.keys()):
            blockType = rtn[blockID]["type"]
            self.logger.debug(f"(DSP block attribute query: {i + 1}/{len(aliases)}) {blockID} -> {blockType}")

            # Control blocks that supports mute/level control
            # (LevelControl, MuteControl, Dante in/out, USB in/out)
            # TODO: AVB and CobraNet in and out should be supported too (need to test on supported hardware)
            if blockType in ["LevelControl", "MuteControl", "DanteInput", "DanteOutput", "UsbInput", "UsbOutput", "AudioOutput"]:

                # Definitely supported
                rtn[blockID]["supported"] = True

                # Ganged controls?
                # (only supported by a subset of blocks)
                if blockType in ["LevelControl", "MuteControl"]:
                    _, _, rtn[blockID]["ganged"] = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get ganged"))
                    rtn[blockID]["ganged"] = bool(rtn[blockID]["ganged"])

                # USB blocks have connected and streaming status flags
                if blockType in ["UsbInput", "UsbOutput"]:
                    rtn[blockID]["usb"] = {
                        "streaming" : False,
                        "connected" : False
                    }

                # Channel info
                _, _, chanCount = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get numChannels"))
                chanCount = int(chanCount)
                channels = {}
                for i in range(1, chanCount + 1):

                    # What can we query for channel name / label?
                    # by default, it's "label"
                    cNameQuery = "label"

                    # Dante uses channelName instead of label
                    if blockType in ["DanteInput", "DanteOutput"]:
                        cNameQuery = "channelName"

                    # Built-in & USB channels don't support labels at all (why, Biamp, why?!?)
                    elif blockType in ["UsbInput", "UsbOutput", "AudioOutput"]:
                        cNameQuery = False

                    # Query channel name/label (if needed/possible)
                    if cNameQuery:
                        _, _, chanLabel = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get {cNameQuery} {i}"))
                    else:
                        # Some blocks don't support channel naming
                        # so we substitute with a placeholder
                        # to prevent downstream stuff from breaking
                        chanLabel = f"Channel{i}"

                    channels[i] = {
                        "idx" : i,
                        "label" : chanLabel
                    }

                    # Mute status is mostly there, UNLESS it's a USB block (quirk, subscription impossible
                    # and we're not just going to poll that...)
                    if "Usb" not in blockType:
                        channels[i]["muted"] = False

                    # Blocks with level control should support current, minimum, and maximum levels
                    if blockType in ["LevelControl", "DanteInput", "DanteOutput", "AudioOutput"]:
                        channels[i]["level"] = {
                            "current" : -100.0
                        }
                        _, _, minLevel = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get minLevel {i}"))
                        _, _, maxLevel = self.__parseResponse(self.__connection.send_wait(f"\"{blockID}\" get maxLevel {i}"))
                        channels[i]["level"]["minimum"] = self.__valFormat(minLevel)
                        channels[i]["level"]["maximum"] = self.__valFormat(maxLevel)

                rtn[blockID]["channels"] = channels

        # Save DSP block information in the cache directory
        if cache:
            pathlib.Path(".cache").mkdir(parents = True, exist_ok = True)
            with open(f".cache/{self.__hostname}.cdspblk", "w") as f:
                json.dump({
                    "blocks" : rtn,
                    "hostname" : self.__hostname,
                    "firmware" : self.__version,
                    "nAliases" : len(self.__dspAliases)
                }, f, indent = 4)
            self.logger.info(f"DSP attributes saved: {self.__hostname}.cdspblk")

        return rtn

    def __setupSubscriptions(self):
        """
        Setup subscriptions to DSP block status updates
        """
        self.logger.debug("setting up subscriptions")

        blocks = 0
        for blockID, blockAttribute in self.__dspBlocks.items():

            # These block types support levels and mutes monitoring (all channels)
            # so we subscribe to both...
            if blockAttribute["type"] in ["LevelControl", "DanteInput", "DanteOutput", "AudioOutput"]:
                self.__connection.send(self.__getSubscribeCommand(blockID, "levels"))
                self.__connection.send(self.__getSubscribeCommand(blockID, "mutes"))
                blocks += 1
                self.logger.debug(f"level/mute subscription: {blockID}")
            
            # Subscribe to mute states for muteControl
            elif blockAttribute["type"] == "MuteControl":
                self.__connection.send(self.__getSubscribeCommand(blockID, "mutes"))
                blocks += 1
                self.logger.debug(f"mute state subscription: {blockID}")

            # Subscribe to USB I/O streaming and connected states
            elif blockAttribute["type"] in ["UsbInput", "UsbOutput"]:
                self.__connection.send(self.__getSubscribeCommand(blockID, "streaming"))
                self.__connection.send(self.__getSubscribeCommand(blockID, "connected"))
                blocks += 1
                self.logger.debug(f"USB subscription: {blockID}")

        self.logger.info(f"subscribed to updates from {blocks} DSP blocks")

    @property
    def info(self):
        """
        Accessor for DSP information
        """
        return {
            "hostname" : self.__hostname,
            "version" : self.__version
        }

    @property
    def ready(self):
        """
        Accessor for ready state (also available publicly)
        """
        return bool(self.__ready)

    @property
    def blocks(self):
        """
        Return a list of all DSP blocks attached
        """
        assert self.ready, "DSP not ready"
        return list(self.__dspBlocks.keys())

    @property
    def supportedBlocks(self):
        """
        Return a list of all supported DSP blocks (as well as their types)
        this is used for the block-get API endpoint
        """
        assert self.ready, "DSP not ready"
        blocks = {}
        for blockID, block in self.__dspBlocks.items():
            if block["supported"]:
                blocks[blockID] = {
                    "type" : block["type"]
                }

        return blocks

    def block(self, blockID : str):
        """
        Get a specific block
        """
        assert self.ready, "DSP not ready"
        
        if blockID in self.blocks:
            return self.__dspBlocks[blockID]
        else:
            # Block not found
            self.logger.warning(f"Invalid block access attempt: {blockID}")
            return None

    def setMute(self, blockID : str, channel : int, value : bool = True):
        """
        Set mute attribute on a specific block/channel
        """
        # Make sure block exists
        block = self.block(blockID)
        assert block is not None, f"Block does not exist: {blockID}"

        # Make sure block type is supported and that channel exists
        assert block["type"] in ["LevelControl", "MuteControl", "DanteInput", "DanteOutput", "AudioOutput"], f"Block type {block['type']} does not support muting"

        # CHANNEL - typically, in Tesira land, it starts at 1. Here, we implement a special value of 0,
        # meaning all channels (multiple commands will be sent to make that happen)
        if channel == 0:
            self.logger.debug(f"mute request for all channels of {blockID}")
            channels = list(block["channels"].keys())
        else:
            self.logger.debug(f"mute request for {blockID} channel {channel}")
            assert channel in block["channels"].keys(), f"Invalid channel {channel} for block {blockID}"
            channels = [channel]

        # Send mute command(s)
        for c in channels:
            self.__connection.send(f"\"{blockID}\" set mute {c} {str(value).lower()}")

        # TODO: might be nice to have something here to check state changes,
        # retry commands after a while if it hasn't been done yet, and throw
        # an exception if command timeout has exceeded, but for now we assume it'll work
        self.logger.info(f"set mute on {blockID}: {value}")
        return

    def setLevel(self, blockID : str, channel : int, value : float):
        """
        Set level attribute on a specific block/channel
        """
        # Make sure block exists
        block = self.block(blockID)
        assert block is not None, f"Block does not exist: {blockID}"

        # Ensure value is a float
        value = float(value)

        # Make sure block type is supported and that channel exists
        assert block["type"] in ["LevelControl", "DanteInput", "DanteOutput", "AudioOutput"], f"Block type {block['type']} does not support level control"

        # CHANNEL - typically, in Tesira land, it starts at 1. Here, we implement a special value of 0,
        # meaning all channels (multiple commands will be sent to make that happen)
        if channel == 0:
            self.logger.debug(f"level request for all channels of {blockID}: {value}")
            channels = list(block["channels"].keys())
        else:
            self.logger.debug(f"mute request for {blockID} channel {channel}: {value}")
            assert channel in block["channels"].keys(), f"Invalid channel {channel} for block {blockID}"
            channels = [channel]

        # Send mute command(s)
        for c in channels:

            # Make sure value is valid
            minV = float(block["channels"][c]["level"]["minimum"])
            maxV = float(block["channels"][c]["level"]["maximum"])

            if minV <= value <= maxV:
                self.__connection.send(f"\"{blockID}\" set level {c} {value}")
            else:
                self.logger.warning(f"invalid level setting on {blockID} channel {channel}, must be between {minV} and {maxV}, wanted {value}")

        # TODO: might be nice to have something here to check state changes,
        # retry commands after a while if it hasn't been done yet, and throw
        # an exception if command timeout has exceeded, but for now we assume it'll work
        self.logger.info(f"set level on {blockID} (channels {channels}): {value}")
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
        "streaming" : "USTR",
        "connected" : "UCON",
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

        # RX loop buffer
        self.__rxLoopBuffer = ""

        while not self.__exit.is_set():

            # Only when the connection is up will it make sense to do this
            if self.__connection.active:

                # Sleep a little to let Paramiko run
                time.sleep(0.00001)

                # Read from buffer until that's empty
                while self.__connection.recv_ready:
                    # Note: here we assume it's already decoded by the transport handler
                    self.__rxLoopBuffer += str(self.__connection.recv(self.__connection.readBufferSize))

                # If there us a newline, response has ended
                # go through lines until we get something with either "+OK" or "-ERR"
                # (responses) - everything else is noise
                while "\n" in self.__rxLoopBuffer:
                    np = self.__rxLoopBuffer.find("\n")
                    grab = str(self.__rxLoopBuffer[:np]).strip()
                    self.__rxLoopBuffer = self.__rxLoopBuffer[np + 1:]

                    # If valid line, call RX processor to figure out what the data is
                    # and parse accordingly
                    if grab.startswith("+OK") or grab.startswith("+ERR") or grab.startswith("!"):
                        self.__processReceivedData(grab)

        # Done?
        self.logger.debug("read loop terminated")
        return

    def __processReceivedData(self, buf : str):
        """
        Given a "line buffer" of received data, process as appropriate
        """            
        try:
            parseOK, msgType, msgData = self.__parseResponse(buf)

            # Don't process invalid stuff that we couldn't parse
            if not parseOK:
                return

            self.logger.debug(f"rx process [{msgType}]: {msgData}")

            # Process subscription (update states of DSP blocks)
            if msgType == "subscription":

                subscriptionDSPBlockID = msgData["dspBlock"]
                subscriptionDataType = msgData["type"]
                subscriptionDataValue = msgData["value"]

                # Do we have that DSP block?
                if subscriptionDSPBlockID in self.__dspBlocks:
                    dspBlockAttributes = self.__dspBlocks[subscriptionDSPBlockID]

                    # Now, depending on the block type, this is processed differently
                    # Let's start with level and mute control blocks
                    if dspBlockAttributes["type"] in ["LevelControl", "MuteControl", "DanteInput", "DanteOutput", "UsbInput", "UsbOutput", "AudioOutput"]:

                        numChannels = len(dspBlockAttributes["channels"])
                        channelIDXs = list(dspBlockAttributes["channels"].keys())

                        # If subscription data value is a list, it's probably part of a multi channel
                        # response of some sort
                        if type(subscriptionDataValue) == list:
                            assert len(subscriptionDataValue) == numChannels, f"{subscriptionDataType} RX channel value mismatch, got {len(subscriptionDataValue)}, expecting {numChannels}"

                            if subscriptionDataType == "mutes":
                                # Another quirk with USB: "all mutes" (or actually mute status streaming)
                                # isn't supported, so we only really process this for non-USB items
                                if dspBlockAttributes["type"] not in ["UsbInput", "UsbOutput"]:
                                    for i, muteStatus in enumerate(subscriptionDataValue):
                                        cIDX = channelIDXs[i]
                                        self.__dspBlocks[subscriptionDSPBlockID]["channels"][cIDX]["muted"] = bool(muteStatus)

                            else:
                                assert dspBlockAttributes["type"] != "MuteControl", "level RX for a mute block?!?"
                                for i, levelStatus in enumerate(subscriptionDataValue):
                                    cIDX = channelIDXs[i]
                                    self.__dspBlocks[subscriptionDSPBlockID]["channels"][cIDX]["level"]["current"] = float(levelStatus)

                        # Otherwise, these should be singular values
                        elif type(subscriptionDataValue) in [str, bool]:

                            # If USB, we also monitor connected and streaming flags
                            if dspBlockAttributes["type"] in ["UsbInput", "UsbOutput"]:
                                if subscriptionDataType == "streaming":
                                    self.__dspBlocks[subscriptionDSPBlockID]["usb"]["streaming"] = bool(subscriptionDataValue)
                                elif subscriptionDataType == "connected":
                                    self.__dspBlocks[subscriptionDSPBlockID]["usb"]["connected"] = bool(subscriptionDataValue)

                    # Updated
                    self.logger.info(f"{subscriptionDSPBlockID} attribute update {subscriptionDataType}: {subscriptionDataValue}")

                else:
                    # Huh? This block doesn't exist?!
                    raise Exception(f"subscription RX for invalid block: {subscriptionDSPBlockID}")

        # Hmm something bad happened
        except Exception as e:
            self.logger.error(f"read process exception: {e} ({traceback.format_exc()})")

    def __valFormat(self, v):
        """
        Function that handles automatic value formatting
        """
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

    def __parseResponse(self, resp):
        """
        Helper function to parse and extract response from the Tesira Text Protocol
        """
        _validResponsePrefixes = ["+OK", "-ERR"]

        # If this is empty, we can't return anything...
        if not resp or str(resp).strip() == "":
            return False, None, None

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
            logging.debug(f"parseResponse no valid lines: {str(resp).strip()}")
            return False, None, None

        # We got something, now if it's an "OK" response, what'd we get?
        if returnType == "ok":

            try:
                dType = line.split(":", 1)[0].replace('"', '')
                dValue = line.split(":", 1)[1].strip()
            except IndexError:
                # This is probably just OK (response to command?)
                if str(line).replace("\"", "").strip() == "":
                    return True, returnType, "cmd_response_ok"
                else:
                    self.logger.warning(f"cannot process OK response: {line}")
                    return False, returnType, None

            if dType == "value":
                # Straight value type
                return True, returnType, self.__valFormat(str(dValue.replace('"', '')))

            elif dType == "list":
                # List type (needs a bit of parsing)
                items = list(re.findall('"([^"]*)"', dValue.split("[", 1)[1].split("]", 1)[0].strip()))
                return True, returnType, [self.__valFormat(i) for i in items]

            else:
                # What is this?!?
                self.logger.warning(f"unknown OK response data type: {dType} -> {line}")
                return True, returnType, line

        # If we get a subscription response, the return will need a bit more parsing
        # as we also need to handle publishToken and list of values
        elif returnType == "subscription":

            rData = {}
            keyvals = list(re.findall('(\[.*?\]|"[^"]*"|[^:\s]+):(\[.*?\]|"[^"]*"|[^,\s]+)', line))

            for item in keyvals:
                assert len(item) == 2, f"Returned item match error: {keyvals} has item with invalid length {len(item)}"
                key = str(item[0]).replace("\"", "").strip()

                # Is return value a list?
                if "[" in str(item[1]):
                    value = list(str(item[1]).replace("\"", "").replace("[", "").replace("]", "").strip().split(" "))
                    value = [self.__valFormat(i) for i in value]
                else:
                    value = self.__valFormat(str(item[1]).replace("\"", ""))

                rData[key] = value

            # This shouldn't happen?
            assert "value" in rData, f"Subscription callback data with no data value: {line}"

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