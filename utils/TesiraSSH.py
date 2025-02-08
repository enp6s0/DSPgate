#!/usr/bin/env python3
import paramiko, time, re, tqdm
from threading import Thread, Event

class TesiraSSH:
    def __init__(self, 
            hostname : str,             # Device hostname or IP
            username : str,             # Username
            password : str,             # Password
            port : int = 22,            # SSH port
            setupCommands : list = [],  # Setup command list
            callback : list = [],       # On-data callback functions
        ):
        """
        Initialize Tesira SSH helper
        """

        # Connection parameters
        self.__hostname = hostname
        self.__username = username
        self.__password = password
        self.__port = int(port)

        # Exit flag
        self.__stop = Event()

        # Hardcoded stuff
        self.__sshInitTimeout = 10                                                  # SSH initialization timeout (seconds)
        self.__sshCommandTimeout = 5                                                # SSH command timeout (seconds)
        self.__sshWelcomeText = "Welcome to the Tesira Text Protocol Server..."     # Text to wait for to confirm channel is up
        self.__sshReadBufferSize = 4096                                             # SSH buffer size

        # Callback sanity checks
        assert type(setupCommands) == list
        assert type(callback) == list
        for i in setupCommands:
            assert type(i) == str
        for j in callback:
            assert callable(j)

        # Load in callbacks and initial command list
        self.__setupCommands = setupCommands
        self.__callback = callback

        # Internal states and variables
        self.__connected = False
        self.__session = None
        self.__connection = None

        # Useful stuff
        self.blocks = {}        # All DSP blocks and types (available after connection & discovery)

        # Go into main loop
        self.__thread = Thread(target = self.__loop)
        self.__thread.daemon = True
        self.__thread.start()

    @property
    def connected(self):
        """
        Accessor for connected flag
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
        print(f"Starting SSH connection: {self.__hostname}:{self.__port} (as {self.__username})")
        self.__connected = False
        self.__session = paramiko.SSHClient()
        self.__session.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect and start a terminal session
        self.__session.connect(self.__hostname, self.__port, username = self.__username, password = self.__password, timeout = self.__sshInitTimeout)
        self.__connection = self.__session.invoke_shell()
        print("SSH connected")

        # Try to connect and wait until we either get the welcome text, or reached
        # timeout limitations, whichever comes first
        __connInit = time.perf_counter()
        welcomed = False
        while time.perf_counter() - __connInit < self.__sshInitTimeout:
            if self.__connection.active:
                time.sleep(0.1)
                if self.__connection.recv_ready():
                    received = self.__connection.recv(self.__sshReadBufferSize).decode()
                    if self.__sshWelcomeText in received:
                        welcomed = True
                        break
        
        if not welcomed:
            # Uh oh, we didn't get a valid response from the DSP
            raise Exception(f"Timeout waiting for protocol establishment")
        else:
            # Connection OK :)
            print(f"Tesira text protocol session established ({time.perf_counter() - __connInit} sec)")
            
            # Get all block aliases (we'll then call them DSP objects...)
            _aliases = self.__syncCommand("SESSION get aliases\n").split("[")[1].split("]")[0].strip()
            aliases = list(re.findall('"([^"]*)"', _aliases))

            # Discover DSP blocks to figure out what they (and their attributes) are
            for o in tqdm.tqdm(aliases, desc = "Discovering DSP blocks"):
                self.blocks[o] = self.__discoverDSPBlockAttribute(o)

            # Fire setup commands from list
            for cmd in self.__setupCommands:
                if cmd != "":
                    self.write(cmd)
                    print(f"\t... setup command: {cmd}")
    
            self.__connected = True
            print("Session setup complete")

    def __syncCommand(self, cmd : str, rtn : str = "+OK"):
        """
        Wrapper function for SYNCHRONOUS command processor. Note:
        must NOT be used in the main loop, since this assumes we're doing
        ONLY one thing at a time and there are no subscriptions going on yet!

        It does allow for a return type to be specified, as we have cases
        where we (ab)use error responses, for example to find out what
        block type they are...
        """
        self.write(cmd)
        commandSent = time.perf_counter()
        while time.perf_counter() - commandSent < self.__sshCommandTimeout:
            time.sleep(0.1)
            if self.__connection.recv_ready():
                received = str(self.__connection.recv(self.__sshReadBufferSize).decode()).strip()
                for line in received.splitlines():
                    if line.startswith(rtn):
                        return str(line[len(rtn):]).strip()

        # If we're here, timeout happened :(
        raise Exception(f"Synchronous command timed out: {cmd}")

    def __discoverDSPBlockAttribute(self, blockName : str):
        """
        Given a DSP block name, discover attributes and return...
        """

        # Quick input formatting functions
        def getValue(inp):
            return str(inp).split(":")[-1].replace('"', "").strip()

        # What will be returned?
        rData = {
            "supported" : False,
            "type" : "unknown"
        }

        # First, we want to get block type
        _ir = self.__syncCommand(f"{blockName} get BLOCKTYPE", rtn = "-ERR")
        blockInterface = _ir.split(" ")[-1]

        # Hmm, this isn't even provided, so probably an unsupported interface
        if "::Attributes" not in blockInterface:
            return rData

        # Figure out block interface type
        bType = str(blockInterface).replace("Interface::Attributes", "").strip()
        rData["type"] = bType

        # Now, given block interface types, we can figure out what to query from here
        # to get specific attributes. We can also set the "supported" flag which will
        # let the front-end API consumers know if this is currently supported or not

        # Level and mute control
        if bType in ["LevelControl", "MuteControl"]:

            rData["supported"] = True                                                                   # definitely supported
            rData["ganged"] = bool("true" in self.__syncCommand(f"{blockName} get ganged"))             # ganged controls?

            # Channel information
            chanCount = int(self.__syncCommand(f"{blockName} get numChannels").split(":")[-1].strip())  # how many channels?
            rData["channels"] = {}
            for i in range(1, chanCount + 1):
                rData["channels"][i] = {
                    "idx" : i,                                                                          # channel index
                    "label" : getValue(self.__syncCommand(f"{blockName} get label {i}"))                # channel label
                }

        return rData

    def write(self, cmd):
        """
        Write command to device
        """
        self.__connection.send(f"{cmd}\n")

    def __read(self):
        """
        Continually read response data stream and feed those to callbacks
        """
        if not self.__stop.is_set():
            if self.__connection.active:
                time.sleep(0.05)
                if self.__connection.recv_ready():

                    # Receive the data and perform stripping so downstream functions have 
                    # something really nice to work with
                    received = str(self.__connection.recv(self.__sshReadBufferSize).decode()).strip()

                    # Fire callbacks
                    for func in self.__callback:
                        func(received)

    def __loop(self):
        """
        Main loop (runs as a thread forever until told to exit)
        """
        while not self.__stop.is_set():
            try:
                # If not connected, we connect (duh)
                if not self.__connected or self.__connection is None or self.__connection.closed:
                    self.__connect()
                
                # We then want to keep on reading data from this SSH connection
                # Note: the read data function itself handles throttling
                if self.__connected and self.__connection.active:
                    self.__read()

            except KeyboardInterrupt:
                print("Keyboard interrupt received, exiting...")
                self.close()

            except Exception as e:
                # Oh no
                self.__connected = False
                print(f"SSH error: {e}")

                # Wait a bit before we reconnect
                time.sleep(1)

    def close(self):
        """
        Properly close out the connection
        """
        self.__stop.set()
        
        if self.__connection and not self.__connection.closed:
            self.__connection.close()

        if self.__session:
            self.__session.close()