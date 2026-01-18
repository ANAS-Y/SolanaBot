import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

def run_dummy_server():
    server_address = ('', 8000)
    httpd = HTTPServer(server_address, BaseHTTPRequestHandler)
    httpd.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()