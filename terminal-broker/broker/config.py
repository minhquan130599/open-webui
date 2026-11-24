"""Settings of the terminal broker, from environment variables (defaults = the agreed plan)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass
class Settings:
    # Who may call the broker: Open WebUI, with this key (Bearer, or the first websocket message).
    api_key: str = field(default_factory=lambda: os.getenv('BROKER_API_KEY', ''))
    # The monitor's key for /_broker/* (status, stop, remove, egress). Open WebUI never has it, so users cannot reach
    # these through the terminal proxy.
    admin_key: str = field(default_factory=lambda: os.getenv('BROKER_ADMIN_KEY', ''))

    # Docker Engine API, through docker-socket-proxy (never the raw socket).
    docker_url: str = field(default_factory=lambda: os.getenv('DOCKER_URL', 'http://docker-socket-proxy:2375'))

    # Terminal containers
    image: str = field(default_factory=lambda: os.getenv('TERMINAL_IMAGE', 'ncs-terminal:latest'))
    network: str = field(default_factory=lambda: os.getenv('TERMINAL_NETWORK', 'terminal-net'))
    port: int = field(default_factory=lambda: _int('TERMINAL_PORT', 8000))
    home: str = field(default_factory=lambda: os.getenv('TERMINAL_HOME', '/home/user'))
    cpus: float = field(default_factory=lambda: _float('TERMINAL_CPUS', 1.0))
    memory_mb: int = field(default_factory=lambda: _int('TERMINAL_MEMORY_MB', 2048))
    pids: int = field(default_factory=lambda: _int('TERMINAL_PIDS', 256))
    tmp_mb: int = field(default_factory=lambda: _int('TERMINAL_TMP_MB', 256))
    read_only: bool = field(default_factory=lambda: _bool('TERMINAL_READ_ONLY', True))
    runtime: str = field(default_factory=lambda: os.getenv('TERMINAL_RUNTIME', ''))  # e.g. runsc (gVisor)
    max_sessions: int = field(default_factory=lambda: _int('TERMINAL_MAX_SESSIONS', 4))
    proxy_url: str = field(default_factory=lambda: os.getenv('TERMINAL_PROXY_URL', 'http://egress-proxy:3128'))

    # Capacity
    max_running: int = field(default_factory=lambda: _int('MAX_RUNNING', 10))
    admin_reserved: int = field(default_factory=lambda: _int('ADMIN_RESERVED', 1))
    evict_min_idle: int = field(default_factory=lambda: _int('EVICT_MIN_IDLE_SECONDS', 120))
    wait_seconds: int = field(default_factory=lambda: _int('BUSY_WAIT_SECONDS', 60))

    # Lifecycle
    idle_stop: int = field(default_factory=lambda: _int('IDLE_STOP_SECONDS', 15 * 60))
    stopped_remove: int = field(default_factory=lambda: _int('STOPPED_REMOVE_SECONDS', 24 * 3600))
    volume_remove: int = field(default_factory=lambda: _int('VOLUME_REMOVE_SECONDS', 7 * 24 * 3600))
    reap_every: int = field(default_factory=lambda: _int('REAP_EVERY_SECONDS', 60))

    # Disk
    disk_quota_mb: int = field(default_factory=lambda: _int('DISK_QUOTA_MB', 5 * 1024))
    disk_check_every: int = field(default_factory=lambda: _int('DISK_CHECK_SECONDS', 600))

    # Egress log (squid access log, read only)
    egress_log: str = field(default_factory=lambda: os.getenv('EGRESS_LOG', '/var/log/squid/access.log'))
    upload_alert_mb: int = field(default_factory=lambda: _int('UPLOAD_ALERT_MB_PER_HOUR', 50))

    # State
    data_dir: str = field(default_factory=lambda: os.getenv('BROKER_DATA', '/data'))

    @property
    def memory_bytes(self) -> int:
        return self.memory_mb * 1024 * 1024


settings = Settings()
