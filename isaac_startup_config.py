from __future__ import annotations
# isaac_startup_config.py

# Helper functions for dynamic port and host configuration

def get_port_and_host(config_path):
    """Extracts port and host settings from the given configuration file."""
    port = None
    host = None
    with open(config_path, 'r') as file:
        for line in file:
            if line.startswith('port='):
                port = line.split('=')[1].strip()
            elif line.startswith('host='):
                host = line.split('=')[1].strip()
    return port, host

# Example usage
# config_path = 'kernel.cfg.monitor'
# port, host = get_port_and_host(config_path)
# print(f"Connecting to {host} on port {port}")
