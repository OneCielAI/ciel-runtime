---
type: Design and Verification
title: Work web TLS and ACME transport
description: Direct certificate TLS, public-IP ACME lifecycle and the verified boundary between implementation and live issuance.
timestamp: 2026-11-10T03:40:00Z
tags: [tls, acme, http-01, lets-encrypt, security]
---

# Modes

- `http`: existing direct HTTP.
- `tailscale`: existing Tailscale Serve TLS proxy.
- `certificate`: load and validate an operator-provided PEM chain/private key and serve HTTPS on the router port.
- `acme-ip`: use the managed ACME account and certificate stores and serve HTTPS on the router port with the activated bundle.

# One port, two transports

The first implementation wrapped the whole listening socket and switched the client base to `https://`. Code review found that this would break local clients: Claude Code, Codex and the router's own health checks dial `127.0.0.1`, and a certificate for a public IP or DNS name cannot verify for that address (ACME refuses loopback identifiers). It was replaced before release:

- `web_tls_runtime.admit_connection` peeks at the first byte of each accepted connection.
  - A TLS handshake record (`0x16`) is wrapped with the validated certificate.
  - Plain HTTP is served only to this host: a loopback peer, or a peer whose address equals the bound address itself.
  - Plain HTTP from any other peer gets `400 HTTPS is required on this port` and is closed.
- The handshake/peek has a 15-second timeout, and the wrapped socket is closed after its request.
- `ROUTER_BASE` stays `http://127.0.0.1:<port>`, so launched clients are unchanged.
- Router start prints `web_direct_tls: https://<name>:<port>/` for each configured name and `web_direct_tls_plain_http: this host only`.

# Certificate safeguards

`tls_runtime.py`:
- validates configured DNS/IP SANs, validity window and matching private key;
- rejects symlinks, missing files and insecure Unix private-key modes;
- requires TLS 1.2 or newer and disables compression;
- activates a new generation only after validation, with an atomic active manifest;
- returns only fingerprint, SAN and expiry status, never paths or private material.

# ACME safeguards

`acme_runtime.py` owns:
- public-only IP/DNS validation (private, loopback, link-local, multicast and wildcard rejected);
- directory/profile capability validation;
- owner-restricted account storage;
- explicit in-memory HTTP-01 tokens and a dedicated port-80 listener active only during authorization;
- stdlib RSA/JWS ACME v2 adapter, order/finalize/certificate download;
- single-flight issue/renew, short-lived renewal threshold, atomic TLS activation;
- last-known-good certificate retention and redacted errors.

# Verified on 2026-11-09

- Live Let's Encrypt staging and production directories both advertised `classic`, `shortlived`, and `tlsserver`, and exposed `newOrder`.
- The official challenge documentation fetched during the task states HTTP-01 can validate IP addresses and operates on port 80.
- Unit/integration tests verify generated account-key signing, JWS account request, HTTP-01 token serving, profile checks, issuance/renewal state, single-flight, redaction and TLS handshake metadata.

- Owned real router (`evidence/e2e_tls_router.py`): `ciel_runtime.py serve` from this checkout with an isolated config directory and `tls_mode=certificate`.
  - Plain local `/health`: 200.
  - HTTPS on the same port: TLS 1.3, verified with the configured certificate (SAN `router.example.test`, `127.0.0.1`); `/health` 200.
  - REST v1 `node-add` over HTTPS: 200.
  - The Work console loaded over HTTPS, with a live event stream and the created goal: [screenshot](evidence/03-work-console-direct-tls.png).
  - A client without that trust anchor was rejected (`self-signed certificate`).
  - Results: [JSON](evidence/tls-router-result.json); start log: [serve log](evidence/tls-router-serve.log).
- The external-plaintext refusal is covered by `tests/test_web_tls_runtime.py`. This machine cannot be a non-local peer to itself.

# Not yet verified

A complete public issuance was not run because this machine has no user-selected public IP target for the certificate and doing so would create an external CA account/order and require public port 80. The adapter and staging directory are verified; an owned public-IP issuance remains an explicit real-environment acceptance step.
