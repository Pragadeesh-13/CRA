import socket
import uvicorn
from app.config import settings


def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def find_available_port(start_port: int, max_attempts: int = 10) -> int:
    for offset in range(max_attempts):
        candidate = start_port + offset
        if not is_port_in_use(candidate):
            return candidate
    return start_port


if __name__ == "__main__":
    target_port = settings.port
    if is_port_in_use(target_port):
        alt_port = find_available_port(target_port + 1)
        print(f"[!] Port {target_port} is already in use by another process.")
        print(f"--> Switching to available port: {alt_port}")
        target_port = alt_port

    print(f"Starting Complaint Resolution Agent (CRA) on port {target_port} (auth_mode: {settings.auth_mode})...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=target_port, reload=True)
