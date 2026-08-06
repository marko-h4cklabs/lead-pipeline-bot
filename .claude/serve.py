"""Minimalni HTTP server za webapp/ koji čita PORT iz env (za Claude preview_start)."""
import http.server, os, socketserver

PORT = int(os.environ.get("PORT", 8080))
os.chdir(os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "webapp"))

Handler = http.server.SimpleHTTPRequestHandler
Handler.log_message = lambda *a: None  # tihi output

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Voice Stitcher na http://localhost:{PORT}", flush=True)
    httpd.serve_forever()
