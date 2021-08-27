# -*- coding: utf-8 -*-
import multiprocessing
import socket

from contextlib import closing
from werkzeug import Request, Response, run_simple


@Request.application
def echo_application(request):
    return Response(
        request.get_data(),
        200,
        content_type=request.content_type)


class WerkzeugServer(object):
    """Realistic WSGI server for unit testing."""
    SOCKET_CONNECT_TIMEOUT = 5

    def __init__(self, application, host='localhost', port=0):
        super(WerkzeugServer, self).__init__()

        self.host = host
        self.port = port

        # Werkzeug will not automatically pick a valid port for us.
        if not self.port:
            sock = socket.socket()
            sock.bind((self.host, self.port))
            self.port = sock.getsockname()[1]

            # Try to close the socket.  Ignore errors.
            try:
                sock.close()
            except IOError:
                pass

        self.process = multiprocessing.Process(
            target=run_simple,
            args=(self.host, self.port, application))

    @classmethod
    def echo_server(cls):
        return WerkzeugServer(echo_application)

    def _socket_is_ready(self):
        with closing(socket.socket()) as sock:
            sock.settimeout(2)
            if sock.connect_ex((self.host, self.port)) == 0:
                return True
            else:
                return False

    def __enter__(self):
        self.process.start()

        # Confirm that we can actually connect to the socket before we return.
        # This protects from flaky tests should the process come up too late.
        while not self._socket_is_ready(): 
            pass

        return self.host, self.port

    def __exit__(self, exc_type, exc_value, traceback):
        self.process.terminate()
        return False
