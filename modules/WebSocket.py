# MIT License

# Copyright (c) 2017 Balazs Bucsay

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import sys

if "WebSocket.py" in sys.argv[0]:
	print "[-] Instead of poking around just try: python xfltreat.py --help"
	sys.exit(-1)

import socket
import time
import select
import os
import struct
import threading

#local files
import Stateful_module
import TCP_generic
import client
import common
import support.websocket_proto as WebSocket_proto

class WebSocket_thread(TCP_generic.TCP_generic_thread):
	def __init__(self, threadID, serverorclient, tunnel, packetselector, comms_socket, client_addr, auth_module, verbosity, config, module_name):
		super(WebSocket_thread, self).__init__(threadID, serverorclient, tunnel, packetselector, comms_socket, client_addr, auth_module, verbosity, config, module_name)
		'''
		threading.Thread.__init__(self)
		self._stop = False
		self.threadID = threadID
		self.tunnel_r = None
		self.tunnel_w = tunnel
		self.packetselector = packetselector
		self.comms_socket = comms_socket
		self.client_addr = client_addr
		self.auth_module = auth_module
		self.verbosity = verbosity
		self.serverorclient = serverorclient
		self.config = config
		self.module_name = module_name
		self.check_result = None
		self.timeout = 3.0

		self.client = None
		self.authenticated = False
		'''
		self.WebSocket_proto = WebSocket_proto.WebSocket_Proto()

		return

	def communication_initialization(self):
		self.client = client.Client()
		self.client.set_socket(self.comms_socket)
		
		common.internal_print("Waiting for upgrade request", 0, self.verbosity, common.DEBUG)
		response = self.comms_socket.recv(4096)
		handshake_key = self.WebSocket_proto.get_handshake_init(response)
		if handshake_key == None:
			common.internal_print("No WebSocket-Key in request", -1, self.verbosity, common.DEBUG)
			self.cleanup()
			sys.exit(-1)

		handshake = self.WebSocket_proto.calculate_handshake(handshake_key)
		response = self.WebSocket_proto.switching_protocol(handshake)
		self.comms_socket.send(response)

		return

	# check request: generating a challenge and sending it to the server
	# in case the answer is that is expected, the targer is a valid server
	def do_check(self):
		message, self.check_result = self.checks.check_default_generate_challenge()
		self.send(common.CONTROL_CHANNEL_BYTE, common.CONTROL_CHECK+message, None)

		return

	# basic authentication support. mostly placeholder for a proper 
	# authentication. Time has not come yet.
	def do_auth(self):
		message = self.auth_module.send_details(self.config.get("Global", "clientip"))
		self.send(common.CONTROL_CHANNEL_BYTE, common.CONTROL_AUTH+message, None)

		return

	# Polite signal towards the server to tell that the client is leaving
	# Can be spoofed? if there is no encryption. Who cares?
	def do_logoff(self):
		self.send(common.CONTROL_CHANNEL_BYTE, common.CONTROL_LOGOFF, None)

		return

	def send(self, channel_type, message, additional_data):
		if channel_type == common.CONTROL_CHANNEL_BYTE:
			transformed_message = self.transform(common.CONTROL_CHANNEL_BYTE+message, 1)
		else:
			transformed_message = self.transform(common.DATA_CHANNEL_BYTE+message, 1)

		websocket_msg = self.WebSocket_proto.build_message(self.serverorclient, 2, transformed_message)

		common.internal_print("WebSocket sent: {0} -> {1}".format(len(transformed_message), len(websocket_msg)), 0, self.verbosity, common.DEBUG)
		
		return self.comms_socket.send(websocket_msg)

	def recv(self):
		data = ""
		length2b = self.comms_socket.recv(2, socket.MSG_PEEK)

		if len(length2b) == 0:
			if self.serverorclient:
				common.internal_print("Client lost. Closing down thread.", -1)
			else:
				common.internal_print("Server lost. Closing down.", -1)
			self.stop()
			self.cleanup()

			return ""

		if len(length2b) != 2:

			return ""

		length_type = self.WebSocket_proto.get_length_type(length2b)
		if length_type == -1:
			common.internal_print("Malformed WebSocket packet", -1, self.verbosity, common.DEBUG)
			return ""

		masked = self.WebSocket_proto.is_masked(length2b)
		header_length = self.WebSocket_proto.get_header_length(masked, length_type)
		header = self.comms_socket.recv(header_length, socket.MSG_PEEK)
		if len(header) != header_length:
			common.internal_print("Malformed WebSocket packet: wrong header length", -1, self.verbosity, common.DEBUG)
			return ""

		data_length = self.WebSocket_proto.get_data_length(header, masked, length_type)
		length = data_length + header_length

		received = 0
		while received < length:
			data += self.comms_socket.recv(length-received)
			received = len(data)

		if length != len(data)	:
			common.internal_print("Error length mismatch", -1)
			return ""

		message = self.WebSocket_proto.get_data(data, header_length, data_length)
		common.internal_print("WebSocket read: {0} -> {1}".format(len(data), len(message)), 0, self.verbosity, common.DEBUG)
		
		return self.transform(message,0)


	def cleanup(self):
		try:
			self.comms_socket.close()
		except:
			pass
		try:
			os.close(self.packetselector.get_pipe_w())		
		except:
			pass

		if self.serverorclient:
			self.packetselector.delete_client(self.client)

	def communication(self, is_check):
		rlist = [self.comms_socket]
		wlist = []
		xlist = []

		while not self._stop:
			if self.tunnel_r:
				rlist = [self.tunnel_r, self.comms_socket]
			try:
				readable, writable, exceptional = select.select(rlist, wlist, xlist, self.timeout)
			except select.error, e:
				break	
			if self._stop:
				self.comms_socket.close()
				break
			try:
				for s in readable:
					if (s is self.tunnel_r) and not self._stop:
						message = os.read(self.tunnel_r, 4096)
						while True:
							if (len(message) < 4) or (message[0:1] != "\x45"): #Only care about IPv4
								break
							packetlen = struct.unpack(">H", message[2:4])[0] # IP Total length
							if packetlen > len(message):
								message += os.read(self.tunnel_r, 4096)
							readytogo = message[0:packetlen]
							message = message[packetlen:]
							self.send(common.DATA_CHANNEL_BYTE, readytogo, None)

					if (s is self.comms_socket) and not self._stop:
						message = self.recv()
						if len(message) == 0:
							continue

						if common.is_control_channel(message[0:1]):
							if self.controlchannel.handle_control_messages(self, message[len(common.CONTROL_CHANNEL_BYTE):], None):
								continue
							else:
								self.stop()
								break

						if self.authenticated:
							try:
								os.write(self.tunnel_w, message[len(common.CONTROL_CHANNEL_BYTE):])
							except OSError as e:
								print e # wut?

			except (socket.error, OSError, IOError):
				if self.serverorclient:
					common.internal_print("Client lost. Closing down thread.", -1)
					self.cleanup()

					return
				if not self.serverorclient:
					common.internal_print("Server lost. Closing connection.", -1)
					self.comms_socket.close()
				break
			except:
				print "another error"
				raise

		self.cleanup()

		return True

class WebSocket(TCP_generic.TCP_generic):

	module_name = "WebSocket"
	module_configname = "WebSocket"
	module_description = """
		"""

	def __init__(self):
		super(WebSocket, self).__init__()
		self.server_socket = None
		self.WebSocket_proto = WebSocket_proto.WebSocket_Proto()

		return

	def stop(self):
		self._stop = True

		if self.threads:
			for t in self.threads:
				t.stop()
		
		# not so nice solution to get rid of the block of accept()
		# unfortunately close() does not help on the block
		try:
			server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			server_socket.connect((self.config.get("Global", "serverbind"), int(self.config.get(self.get_module_configname(), "serverport"))))
		except:
			pass

		return

	def websocket_upgrade(self, server_socket):

		request = self.WebSocket_proto.upgrade("/chat", "xfltreat.info", "xfltreat.info", 13)
		server_socket.send(request)
		
		response = server_socket.recv(4096)
		if response[0:12] != "HTTP/1.1 101":
			common.internal_print("Connection failed: {0}".format(response[0:response.find("\n")]), -1)

			return False

		return True

	def sanity_check(self):
		if not self.config.has_option(self.get_module_configname(), "serverport"):
			common.internal_print("'serverport' option is missing from '{0}' section".format(self.get_module_configname()), -1)

			return False

		try:
			convert = int(self.config.get(self.get_module_configname(), "serverport"))
		except:
			common.internal_print("'serverport' is not an integer in '{0}' section".format(self.get_module_configname()), -1)
			return False

		return True

	def serve(self):
		client_socket = server_socket = None
		self.threads = []
		threadsnum = 0

		if not self.sanity_check():
			return 

		common.internal_print("Starting module: {0} on {1}:{2}".format(self.get_module_name(), self.config.get("Global", "serverbind"), int(self.config.get(self.get_module_configname(), "serverport"))))
		
		server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		try:
			server_socket.bind((self.config.get("Global", "serverbind"), int(self.config.get(self.get_module_configname(), "serverport"))))
			while not self._stop:
				server_socket.listen(1) #?? 1 ??
				client_socket, client_addr = server_socket.accept()
				common.internal_print(("Client connected: {0}".format(client_addr)), 0, self.verbosity, common.DEBUG)

				threadsnum = threadsnum + 1
				thread = WebSocket_thread(threadsnum, 1, self.tunnel, self.packetselector, client_socket, client_addr, self.auth_module, self.verbosity, self.config, self.get_module_name())
				thread.start()
				self.threads.append(thread)

		except socket.error as exception:
			# [Errno 98] Address already in use
			if exception.args[0] != 98:
				raise
			else:
				common.internal_print("Starting failed, port is in use: {0} on {1}:{2}".format(self.get_module_name(), self.config.get("Global", "serverbind"), int(self.config.get(self.get_module_configname(), "serverport"))), -1)

		self.cleanup(server_socket)

		return

	def connect(self):
		try:
			if not self.sanity_check():
				return 
			common.internal_print("Starting client: {0}".format(self.get_module_name()))

			client_fake_thread = None

			server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			server_socket.settimeout(3)
			server_socket.connect((self.config.get("Global", "remoteserverip"), int(self.config.get(self.get_module_configname(), "serverport"))))

			if self.websocket_upgrade(server_socket):
				client_fake_thread = WebSocket_thread(0, 0, self.tunnel, None, server_socket, None, self.auth_module, self.verbosity, self.config, self.get_module_name())
				client_fake_thread.do_auth()
				client_fake_thread.communication(False)

		except KeyboardInterrupt:
			if client_fake_thread:
				client_fake_thread.do_logoff()
			self.cleanup(server_socket)
			raise
		except socket.error:
			common.internal_print("Connection error: {0}".format(self.get_module_name()), -1)
			self.cleanup(server_socket)
			raise

		self.cleanup(server_socket)

		return

	def check(self):
		try:
			if not self.sanity_check():
				return 
			common.internal_print("Checking module on server: {0}".format(self.get_module_name()))

			server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			server_socket.settimeout(3)
			server_socket.connect((self.config.get("Global", "remoteserverip"), int(self.config.get(self.get_module_configname(), "serverport"))))
			
			if self.websocket_upgrade(server_socket):
				client_fake_thread = WebSocket_thread(0, 0, None, None, server_socket, None, self.auth_module, self.verbosity, self.config, self.get_module_name())
				client_fake_thread.do_check()
				client_fake_thread.communication(True)

			self.cleanup(server_socket)

		except socket.timeout:
			common.internal_print("Checking failed: {0}".format(self.get_module_name()), -1)
			self.cleanup(server_socket)
		except socket.error as exception:
			if exception.args[0] == 111:
				common.internal_print("Checking failed: {0}".format(self.get_module_name()), -1)
			else:
				common.internal_print("Connection error: {0}".format(self.get_module_name()), -1)
			self.cleanup(server_socket)

		return

	def cleanup(self, socket):
		common.internal_print("Shutting down module: {0}".format(self.get_module_name()))
		socket.close()

		return