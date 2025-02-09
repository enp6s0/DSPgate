#!/usr/bin/env python3
from utils.TesiraConnectionHandle import *
from threading import Thread, Event
import time, datetime, paramiko

class SSH(TesiraConnectionHandle):
    """
    SSH based transport
    """

    def __init__(self, 
            hostname : str,             # Device hostname or IP
            username : str,             # Username
            password : str,             # Password
            port : int = 22,            # SSH port
        ):

        # Init base stuff
        super().__init__()

        # Connection parameters
        self.__hostname = hostname
        self.__username = username
        self.__password = password
        self.__port = int(port)
        self.logger.debug(f"hostname = '{self.__hostname}', port = '{self.__port}', username = '{self.__username}'")

        # Stop Paramiko from flooding the terminal with its debug messages
        # (we typically don't have to debug that deep...)
        logging.getLogger("paramiko.transport").setLevel(logging.INFO)

        # Internal states and variables
        self.__connected = False
        self.__session = None
        self.__connection = None
        self.__exit = Event()

        # Go into main loop
        self.__thread = Thread(target = self.__loop)
        self.__thread.daemon = True
        self.__thread.start()

        # Done!
        return

    @property
    def active(self):
        """
        Connection running and everything is fine?
        """
        return bool(self.__connected)

    def __connect(self):
        """
        (re) initialize SSH connection to the DSP
        """

        # If there's a lingering session, we want to try and close that
        # (no big deal if it fails)
        if self.__session:
            try:
                self.__session.close()
            except:
                pass

        # Start from disconnected state
        self.__connected = False
        self.__session = paramiko.SSHClient()
        self.__session.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect and start a terminal session
        self.__session.connect(self.__hostname, self.__port, username = self.__username, password = self.__password, timeout = self.initialConnectionTimeout)
        self.__connection = self.__session.invoke_shell()
        self.logger.debug(f"starting")

        # Try to connect and wait until we either get the welcome text, or reached
        # timeout limitations, whichever comes first
        __connInit = time.perf_counter()
        welcomed = False
        self.logger.info(f"waiting for session establishment")
        while time.perf_counter() - __connInit < self.initialConnectionTimeout:
            if self.__connection.active:
                time.sleep(0.1)
                if self.__connection.recv_ready():
                    received = self.__connection.recv(self.readBufferSize).decode()
                    if self.protocolWelcomeText in received:
                        welcomed = True
                        break
        
        if not welcomed:
            # Uh oh, we didn't get a valid response from the DSP
            raise Exception(f"timeout waiting for session establishment")
            self.__connected = False
        else:
            # Connection OK :)
            self.logger.info(f"Tesira text protocol session established ({time.perf_counter() - __connInit} sec)")
            self.__connected = True

    def __loop(self):
        """
        Main loop (runs as a thread forever until told to exit)
        """
        self.logger.debug("entering main loop")

        while not self.__exit.is_set():
            try:
                # If not connected, we connect (duh)
                if not self.__connected or self.__connection is None or self.__connection.closed:
                    self.__connect()
                
                # That's just about it, everything else will be called by the higher level
                # handler, so we don't need to handle reading in here (yay!)
                # (we do want to be somewhat gentle on the CPU usage though)
                time.sleep(0.2)

            except KeyboardInterrupt:
                print("keyboard interrupt received, exiting...")
                self.close()

            except Exception as e:
                # Oh no
                self.__connected = False
                self.logger.error(f"ERROR: {e}")

                # Wait a bit before we reconnect
                time.sleep(1)

    @property
    def recv_ready(self):
        """
        Data ready in read buffer?
        """
        if self.__connected and self.__connection.active:
            return self.__connection.recv_ready()
        else:
            return False

    def recv(self, bufsize):
        """
        Read data from RX buffer
        """
        if self.__connected and self.__connection.active:
            return str(self.__connection.recv(self.readBufferSize).decode()).strip()
        else:
            raise Exception("device not ready")

    def send(self, data):
        """
        Send data to device
        """
        self.logger.debug(f"send: {data}")
        if self.__connected and self.__connection.active:
            self.__connection.send(f"{data}\n")
        else:
            raise Exception("device not ready")

        return

    def send_wait(self, data):
        """
        Send data and wait for response
        """
        self.logger.debug(f"send_wait: {data}")
        self.send(data)
        commandSent = time.perf_counter()
        while time.perf_counter() - commandSent < self.commandTimeout:
            time.sleep(0.1)
            if self.__connection.recv_ready():
                received = str(self.__connection.recv(self.readBufferSize).decode()).strip()
                return received

        # If we're here, timeout happened :(
        raise Exception(f"command timeout: {cmd}")
        return

    def close(self):
        """
        Close device connection and kill thread
        """
        # Wait for thread to exit
        self.__exit.set()
        self.__thread.join()
        
        # Close connection and session if possible
        if self.__connection and not self.__connection.closed:
            self.__connection.close()

        if self.__session:
            self.__session.close()

        # Done!
        return