#!/usr/bin/env python3
from utils.TesiraConnectionHandle import *
import sys, re, pprint

class Tesira:
    """
    Representative "object" for a Biamp Tesira DSP. Automatically handles configuration query,
    state updates, and such in the background
    """

    def __debugPrint(self, msg):
        """
        Debug-print helper that only prints stuff out if debug mode is enabled
        """
        caller = sys._getframe(1).f_globals["__name__"]
        if self.__debug:
            print(f"[DBG][{caller}] {msg}")

    def __parseResponse(self, resp):
        """
        Helper function to parse and extract response from the Tesira Text Protocol
        """
        _validResponsePrefixes = ["+OK", "-ERR"]

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
                return True, returnType, str(dValue.replace('"', '')).strip()

            elif dType == "list":
                # List type (needs a bit of parsing)
                items = list(re.findall('"([^"]*)"', dValue.split("[", 1)[1].split("]", 1)[0].strip()))
                return True, returnType, list(items)

            else:
                # What is this?!?
                self.__debugPrint(f"unknown OK response data type: {dType} -> {line}")
                return True, returnType, line

        else:
            # TODO: this needs fleshing out
            return True, returnType, line    

    def __init__(self, connection : TesiraConnectionHandle, dspAttributesFile : str = None, debug : bool = False):
        """
        Initializer function

        This is a bit big - it first starts the communication channel,
        wait for that channel to be active, then query the device for many many
        things, from device ID to ALL DSP blocks

        After this is done, it'll call another function to set up subscription
        to keep us updated on the current state of things
        """
        # Debug?
        self.__debug = bool(debug)

        # Backend connection (calling this will also start it)
        self.__connection = connection

        # Wait for init to complete
        self.__debugPrint("waiting for connection")
        while not self.__connection.active:
            time.sleep(0.1)
        self.__debugPrint("connection confirmed")

        # Query hostname
        _, _, self.__hostname = self.__parseResponse(self.__connection.send_wait("DEVICE get hostname"))
        self.__debugPrint(f"device hostname: {self.__hostname}")

        # Query version
        _, _, self.__version = self.__parseResponse(self.__connection.send_wait("DEVICE get version"))
        self.__debugPrint(f"device firmware version: {self.__version}")

        # Query DSP block IDs/names
        _, _, self.__dspAliases = self.__parseResponse(self.__connection.send_wait("SESSION get aliases"))
        self.__debugPrint(f"found {len(self.__dspAliases)} attribute aliases")

        # This step will take a long time - we query ALL DSP blocks and their attributes. To save time,
        # this can be optionally cached in cases where the configuration is expected to be static
        self.__dspBlocks = {}
        if dspAttributesFile is None:

            # Traverse all DSP blocks and discover types
            for i, blockID in enumerate(self.__dspAliases):

                # Intentionally send an invalid command to get interface info
                _, _, resp =  self.__parseResponse(self.__connection.send_wait(f"{blockID} get BLOCKTYPE"))
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
                self.__debugPrint(f"(DSP block discovery: {i + 1}/{len(self.__dspAliases)}) {blockID} -> {blockType}")

            # Now, for supported types, discover their attributes
            for i, blockID in enumerate(self.__dspBlocks.keys()):
                blockType = self.__dspBlocks[blockID]["type"]
                self.__debugPrint(f"(DSP block attribute query: {i + 1}/{len(self.__dspBlocks)}) {blockID} -> {blockType}")
                
                # Level and mute control blocks
                if blockType in ["LevelControl", "MuteControl"]:

                    # Definitely supported
                    self.__dspBlocks[blockID]["supported"] = True

                    # Ganged controls?
                    _, _, self.__dspBlocks[blockID]["ganged"] = self.__parseResponse(self.__connection.send_wait(f"{blockID} get ganged"))
                    self.__dspBlocks[blockID]["ganged"] = bool("true" in self.__dspBlocks[blockID]["ganged"])

                    # Channel info
                    _, _, chanCount = self.__parseResponse(self.__connection.send_wait(f"{blockID} get numChannels"))
                    chanCount = int(chanCount)
                    channels = {}
                    for i in range(1, chanCount + 1):
                        _, _, chanLabel = self.__parseResponse(self.__connection.send_wait(f"{blockID} get label {i}"))
                        channels[i] = {
                            "idx" : i,
                            "label" : chanLabel
                        }
                    self.__dspBlocks[blockID]["channels"] = channels
            
        else:
            # TODO: implement DSP attribute caching/loading to speed up initialization for cases where
            # we know the DSP configuration isn't getting changed willy-nilly
            raise Exception("TODO!")

        # HACK: DEBUG
        pprint.pprint(self.__dspBlocks)
