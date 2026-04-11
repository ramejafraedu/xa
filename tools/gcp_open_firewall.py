#!/usr/bin/env python3
"""Open required GCP firewall ports for Video Factory VM access.

Creates (if missing) two ingress rules in the project network:
- TCP 22 for SSH
- TCP 8000 for dashboard
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

PROJECT_ID = "inductive-actor-492816-j2"
NETWORK = "default"
RULES = [
    {
        "name": "video-factory-allow-ssh",
        "ports": ["22"],
        "priority": 1000,
        "description": "Allow SSH for Video Factory operations",
    },
    {
        "name": "video-factory-allow-8000",
        "ports": ["8000"],
        "priority": 1001,
        "description": "Allow dashboard on port 8000",
    },
]


def load_credentials() -> service_account.Credentials:
    key_path = Path("inductive-actor-492816-j2-9e722d76f0c7.json")
    if not key_path.exists():
        raise FileNotFoundError(f"Service account key missing: {key_path}")
    creds = service_account.Credentials.from_service_account_file(
        str(key_path),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(Request())
    return creds


def main() -> int:
    creds = load_credentials()
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }

    base = f"https://compute.googleapis.com/compute/v1/projects/{PROJECT_ID}/global/firewalls"

    # Fetch existing firewall rule names.
    existing_names: set[str] = set()
    list_resp = requests.get(base, headers=headers, timeout=30)
    if list_resp.status_code == 200:
        for item in list_resp.json().get("items", []):
            name = item.get("name")
            if name:
                existing_names.add(name)
    else:
        print(f"[WARN] Could not list firewall rules: {list_resp.status_code} {list_resp.text[:300]}")

    for rule in RULES:
        if rule["name"] in existing_names:
            print(f"[OK] Rule already exists: {rule['name']}")
            continue

        body = {
            "name": rule["name"],
            "description": rule["description"],
            "network": f"projects/{PROJECT_ID}/global/networks/{NETWORK}",
            "direction": "INGRESS",
            "priority": rule["priority"],
            "sourceRanges": ["0.0.0.0/0"],
            "allowed": [{"IPProtocol": "tcp", "ports": rule["ports"]}],
        }
        resp = requests.post(base, headers=headers, data=json.dumps(body), timeout=30)
        if resp.status_code in (200, 201):
            print(f"[OK] Created rule: {rule['name']}")
        elif resp.status_code == 409:
            print(f"[OK] Rule already exists (race): {rule['name']}")
        else:
            print(f"[ERR] Failed creating {rule['name']}: {resp.status_code} {resp.text[:400]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
