# RouterOS v7.24.2 Guide

This guide explains how to run the DN42 bot agent on a Linux host while managing a MikroTik RouterOS v7.24.2 router through the native RouterOS API.

The Telegram bot server and the agent remain separate services:

- `server/` runs the Telegram bot and sends requests to agents.
- `agent/` runs on Linux and exposes the HTTP API used by the bot.
- RouterOS hosts the WireGuard interface and BGP sessions.

## Requirements

- RouterOS v7.24.2 or a compatible RouterOS v7 release.
- A Linux host running Python 3.10 or newer.
- Network access from the Linux host to RouterOS API-SSL, normally TCP port `8729`.
- A RouterOS WireGuard interface created before starting the agent.
- A RouterOS BGP template created before starting the agent.
- A DNS name or IP address that the server can use to reach the agent HTTP API.

API-SSL is recommended for production. Plain API on port `8728` should only be used on a trusted management network.

## RouterOS preparation

### Enable API-SSL

Use WinBox, WebFig, or the RouterOS terminal to enable API-SSL and restrict access to the Linux agent host or management network:

```routeros
/ip service set api-ssl disabled=no port=8729 address=192.0.2.10/32
```

Replace `192.0.2.10/32` with the address of the Linux host running the agent. Configure a trusted certificate for `api-ssl` when certificate verification is enabled.

For a lab-only setup, plain API can be enabled instead:

```routeros
/ip service set api disabled=no port=8728 address=192.0.2.10/32
```

### Create a dedicated user

Create a separate account for the agent. Do not use the RouterOS `admin` account. The exact policy set may need adjustment for the features enabled on the router, but the account must be able to:

- read interfaces, WireGuard peers, routing, and BGP state;
- add, update, and remove WireGuard peers;
- add, update, disable, and enable BGP connections;
- read and use IP and routing configuration required by the deployment.

Example starting point:

```routeros
/user group add name=dn42-bot policy=api,read,write,test
/user add name=dn42-bot group=dn42-bot password="REPLACE_WITH_A_LONG_PASSWORD"
```

Review the account permissions after a lab test and remove policies that are not required by the final deployment.

### Create the WireGuard interface

Create the interface and assign the local addresses used by the agent. The interface name must match `ROUTEROS_WIREGUARD_INTERFACE`.

```routeros
/interface wireguard add name=dn42 listen-port=51820 mtu=1420
/ip address add address=172.23.246.1/32 interface=dn42
/ipv6 address add address=fe80::1816/64 interface=dn42
/ipv6 address add address=fd2c:1323:4042::1/128 interface=dn42
```

Use your own DN42 addresses and private key material. The public key configured in `MY_WG_PUBLIC_KEY` must be the public key for this RouterOS interface.

The agent adds and updates per-peer WireGuard entries on this interface. Peer comments use the format `dn42-<ASN>`.

### Create the BGP template

Create a RouterOS v7 routing BGP template and set its name in `ROUTEROS_BGP_TEMPLATE`. The template should contain the local ASN and the import/export policy appropriate for your DN42 deployment.

```routeros
/routing/bgp/template add name=dn42-bot as=4242420000
```

Replace `4242420000` with the ASN configured in `ROUTEROS_LOCAL_ASN`. Apply DN42 route filters before enabling production peering.

## Configure the agent

From the repository root:

```bash
cp agent/agent_config.example.json agent/agent_config.json
```

Edit `agent/agent_config.json`. The important RouterOS settings are:

| Setting | Meaning |
| --- | --- |
| `ROUTEROS_HOST` | RouterOS management IP or hostname. |
| `ROUTEROS_PORT` | `8729` for API-SSL or `8728` for plain API. |
| `ROUTEROS_USERNAME` | Dedicated RouterOS API username. |
| `ROUTEROS_PASSWORD` | RouterOS API password. Keep this file private. |
| `ROUTEROS_USE_SSL` | Set to `true` for API-SSL. |
| `ROUTEROS_SSL_VERIFY` | Verify the RouterOS API-SSL certificate. |
| `ROUTEROS_WIREGUARD_INTERFACE` | Existing RouterOS WireGuard interface name. |
| `ROUTEROS_BGP_TEMPLATE` | Existing RouterOS BGP template name. |
| `ROUTEROS_LOCAL_ASN` | Local DN42 ASN used by the BGP template. |
| `ROUTEROS_ROUTING_TABLE_4` | RouterOS IPv4 routing table used for lookups. |
| `ROUTEROS_ROUTING_TABLE_6` | RouterOS IPv6 routing table used for lookups. |

Also set the local DN42 addresses and `MY_WG_PUBLIC_KEY` correctly. Change `SECRET` from the example value and use the same value as `API_TOKEN` in the server configuration.

The agent HTTP listener is controlled by `HOST` and `PORT`. Bind it to a private address or protect it with a firewall; the endpoint uses a shared HTTP header token rather than user authentication.

## Install and start

Install the agent dependencies in a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r agent/requirements.txt
```

Start the agent from the `agent/` directory so it can find `agent_config.json`:

```bash
cd agent
../.venv/bin/python main.py
```

For production, run it under systemd, supervisord, or another process manager. The agent must be able to reach RouterOS and must be reachable by the bot server on `HOST:PORT`.

## Connect the bot server

Configure the server with the agent's address and matching API token. The relevant settings are:

- `SERVERS`: logical node names;
- `HOSTS`: optional explicit agent hostnames or IP addresses;
- `ENDPOINT`: suffix used when `HOSTS` does not contain a node;
- `API_PORT`: the agent HTTP port, normally `54321`;
- `API_TOKEN`: exactly the same value as the agent `SECRET`.

Start the server using its existing deployment procedure after the agent is reachable.

## Verify the installation

Check the agent version:

```bash
curl -i -X POST http://AGENT_HOST:54321/version
```

A successful response contains the current agent version, `28` at the time of writing.

Check RouterOS API connectivity by calling the protected discovery endpoint:

```bash
curl -i -X POST http://AGENT_HOST:54321/pre_peer \
  -H 'X-DN42-Bot-Api-Secret-Token: REPLACE_WITH_AGENT_SECRET'
```

A successful response is JSON containing `existed`, `max`, `net_support`, and `lla`. A `503` response usually means the agent cannot connect to RouterOS or the RouterOS account lacks a required permission.

Before using production peers, test the complete lifecycle with a lab ASN:

1. Create a peer through the bot's `/peer` workflow.
2. Confirm a WireGuard peer with comment `dn42-<ASN>` appears on the configured interface.
3. Confirm a BGP connection with the same name appears in RouterOS.
4. Check `/info` and confirm the WireGuard and BGP state is visible.
5. Modify and restart the peer.
6. Remove the peer and confirm both RouterOS objects are gone.

## Troubleshooting

### `503` from the agent

Check the agent log and verify:

- `ROUTEROS_HOST` and `ROUTEROS_PORT` are correct;
- the API-SSL certificate is trusted when `ROUTEROS_SSL_VERIFY` is `true`;
- the RouterOS firewall allows the Linux host to connect;
- the RouterOS username and password are correct;
- the user has API, read, and write permissions;
- the WireGuard interface and BGP template names exist.

### Peer creation fails

Check that:

- the peer public key is a valid WireGuard public key;
- the endpoint includes a port, for example `198.51.100.20:51820`;
- at least one valid IPv4 or IPv6 peer address was supplied;
- the remote ASN is a valid 32-bit ASN;
- the RouterOS BGP template permits the requested peer;
- DN42 import and export filters are configured.

### `/info` shows no BGP session

The agent identifies BGP resources using the name `dn42-<ASN>`. Confirm that the RouterOS BGP connection has that name and that its remote address matches the peer address configured on WireGuard. Inspect the RouterOS BGP session state and routing logs.

### API-SSL cannot connect

Test the TCP path first:

```bash
nc -vz ROUTEROS_HOST 8729
```

Then check the RouterOS certificate, the `api-ssl` service address restriction, and whether the certificate's name matches `ROUTEROS_HOST`. For a temporary lab diagnosis only, set `ROUTEROS_SSL_VERIFY` to `false`; restore certificate verification for production.

## Security checklist

- Use API-SSL and a trusted certificate.
- Use a dedicated RouterOS user with a long unique password.
- Restrict RouterOS API-SSL by source address.
- Restrict the agent HTTP port to the bot server or a private network.
- Use a long random agent `SECRET` and server `API_TOKEN`.
- Keep `agent_config.json` out of version control and readable only by the agent service account.
- Configure and test DN42 BGP route filters before enabling peering.
- Do not expose RouterOS API or the agent HTTP API directly to the public Internet.
