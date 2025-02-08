#!/usr/bin/env python3
from threading import Thread, Event
import time, sys

class TesiraConnectionHandle:
    """
    Connection handle class, so we can (theoretically) handle multiple transports in the future,
    be it SSH, Telnet, or RS-232. Initially, only SSH is supported
    """
    def __init__(self, debug : bool = False):

        self.transport = None          # connection object

        # Read buffer size
        self.readBufferSize = 4096

        # Welcome text to wait for (protocol specific)
        self.protocolWelcomeText = "Welcome to the Tesira Text Protocol Server..." 

        # Timeouts (all in seconds)
        self.initialConnectionTimeout = 10      # initial connection timeout
        self.commandTimeout = 5                 # command level timeout

        # Debug mode?
        self.__debug = bool(debug)
    
    def debugPrint(self, msg):
        """
        Debug-print helper that only prints stuff out if debug mode is enabled
        """
        caller = sys._getframe(1).f_globals["__name__"]
        if self.__debug:
            print(f"[DBG][{caller}] {msg}")

    @property
    def active(self):
        """
        Connection running and everything is fine?
        """
        return False

    def recv_ready(self):
        """
        Data ready in read buffer?
        """
        raise Exception("Not implemented in base class")

    def recv(self, bufsize):
        """
        Read data from RX buffer
        """
        raise Exception("Not implemented in base class")

    def send(self, data):
        """
        Send data to device
        """
        raise Exception("Not implemented in base class")

    def send_wait(self, data):
        """
        Send a command to device AND wait for results synchronously
        (block until reply or timeout, whichever comes first)
        """
        raise Exception("Not implemented in base class")

    def close(self):
        """
        Close device connection and kill thread
        """
        pass