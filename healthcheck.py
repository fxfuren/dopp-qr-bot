#!/usr/bin/env python3
"""Healthcheck script for Docker container."""
import sys
from pathlib import Path

def main():
    """Check if bot is healthy."""
    try:
        # Check if PID file exists (created by main.py)
        pid_file = Path("/tmp/dopp_bot/bot.pid")
        if not pid_file.exists():
            print("ERROR: PID file not found", file=sys.stderr)
            sys.exit(1)
        
        # Check if PID is valid
        try:
            pid = int(pid_file.read_text().strip())
            # Check if process exists
            Path(f"/proc/{pid}").exists()
        except (ValueError, FileNotFoundError):
            print("ERROR: Invalid or dead PID", file=sys.stderr)
            sys.exit(1)
        
        # Check if heartbeat file is recent (updated every 60s by bot)
        heartbeat_file = Path("/tmp/dopp_bot/heartbeat")
        if heartbeat_file.exists():
            import time
            age = time.time() - heartbeat_file.stat().st_mtime
            if age > 120:  # 2 minutes threshold
                print(f"ERROR: Heartbeat too old ({age:.0f}s)", file=sys.stderr)
                sys.exit(1)
        
        print("OK: Bot is healthy")
        sys.exit(0)
        
    except Exception as e:
        print(f"ERROR: Healthcheck failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
