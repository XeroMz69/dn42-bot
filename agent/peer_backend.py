from ipaddress import ip_address

import base
from routeros import RouterOSClient


class PeerValidationError(ValueError):
    """The server supplied an invalid peer definition."""


def _required(peer_info, key):
    value = peer_info.get(key)
    if value in (None, "", "Not enabled", "Not required due to not use LLA as IPv6"):
        raise PeerValidationError(f"missing peer field: {key}")
    return value


def peer_name(asn):
    try:
        asn = int(asn)
    except (TypeError, ValueError) as exc:
        raise PeerValidationError("ASN must be an integer") from exc
    if asn < 1 or asn > 4294967295:
        raise PeerValidationError("ASN is outside the RouterOS range")
    return f"dn42-{asn}"


def _address_values(peer_info):
    values = []
    for key in ("IPv6", "IPv4"):
        value = peer_info.get(key)
        if value and not value.startswith("Not "):
            try:
                address = ip_address(value)
            except ValueError as exc:
                raise PeerValidationError(f"invalid {key} address") from exc
            values.append(f"{address}/{'128' if address.version == 6 else '32'}")
    if not values:
        raise PeerValidationError("peer has no usable address")
    return values


def _wireguard_values(peer_info):
    values = {
        "interface": base.ROUTEROS_WIREGUARD_INTERFACE,
        "public-key": _required(peer_info, "PublicKey"),
        "allowed-address": ",".join(_address_values(peer_info)),
        "comment": peer_name(peer_info.get("ASN")),
    }
    endpoint = peer_info.get("Clearnet")
    if endpoint:
        if ":" not in endpoint:
            raise PeerValidationError("endpoint must include a port")
        host, port = endpoint.rsplit(":", 1)
        values["endpoint-address"] = host.strip("[]")
        try:
            values["endpoint-port"] = int(port)
        except ValueError as exc:
            raise PeerValidationError("endpoint port must be an integer") from exc
    return values


def _bgp_values(peer_info):
    name = peer_name(peer_info.get("ASN"))
    remote_address = peer_info.get("IPv6")
    if not remote_address or remote_address.startswith("Not "):
        remote_address = _required(peer_info, "IPv4")
    return {
        "name": name,
        "remote.address": str(ip_address(remote_address)),
        "remote.as": int(peer_info["ASN"]),
        "local.role": "ebgp",
        "templates": base.ROUTEROS_BGP_TEMPLATE,
        "comment": name,
    }


def _allowed_addresses(value):
    addresses = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            addresses.append(ip_address(item.split("/", 1)[0]))
        except ValueError:
            continue
    return addresses


class RouterOSPeerBackend:
    """RouterOS WireGuard and BGP operations for one DN42 peer."""

    def __init__(self, client=None):
        self.client = client or RouterOSClient()

    async def _find(self, path, **query):
        rows = await self.client.get(path, **query)
        return rows[0] if rows else None

    async def count(self):
        peers = await self.client.get(
            "/interface/wireguard/peers",
            interface=base.ROUTEROS_WIREGUARD_INTERFACE,
        )
        return len([peer for peer in peers if str(peer.get("comment", "")).startswith("dn42-")])

    async def info(self, asn):
        name = peer_name(asn)
        peer = await self._find("/interface/wireguard/peers", comment=name)
        if not peer:
            return None
        interface = await self._find(
            "/interface/wireguard", name=base.ROUTEROS_WIREGUARD_INTERFACE
        )
        sessions = await self.client.get("/routing/bgp/session", name=name)
        addresses = _allowed_addresses(peer.get("allowed-address"))
        remote_v6 = next((str(address) for address in addresses if address.version == 6), None)
        remote_v4 = next((str(address) for address in addresses if address.version == 4), None)
        established = any(
            str(session.get("state", "")).lower() == "established" for session in sessions
        )
        session_name = name if sessions else ""
        return {
            "port": int(interface.get("listen-port", 0)) if interface else 0,
            "v6": remote_v6,
            "v4": remote_v4,
            "clearnet": (
                f"{peer['endpoint-address']}:{peer['endpoint-port']}"
                if peer.get("endpoint-address") and peer.get("endpoint-port")
                else None
            ),
            "pubkey": peer.get("public-key"),
            "desc": peer.get("comment", name),
            "session": "IPv6 Session with IPv6 & IPv4 Channels" if remote_v6 else "IPv4 Session with IPv4 Channels",
            "session_name": [session_name] if session_name else [],
            "my_v6": str(base.MY_DN42_LINK_LOCAL_ADDRESS),
            "my_v4": str(base.MY_DN42_IPv4_ADDRESS) if remote_v4 else None,
            "my_pubkey": interface.get("public-key") if interface else base.MY_WG_PUBLIC_KEY,
            "wg_last_handshake": int(peer.get("last-handshake", 0) or 0),
            "wg_transfer": [int(peer.get("rx", 0) or 0), int(peer.get("tx", 0) or 0)],
            "bird_status": {
                session_name: ["Established" if established else "Idle", "", {"4": 0, "6": 0}]
            }
            if session_name
            else {},
            "net_support": base.NET_SUPPORT,
            "lla": str(base.MY_DN42_LINK_LOCAL_ADDRESS),
        }

    async def create_or_update(self, peer_info):
        name = peer_name(peer_info.get("ASN"))
        wireguard_values = _wireguard_values(peer_info)
        wireguard = await self._find(
            "/interface/wireguard/peers", comment=wireguard_values["comment"]
        )
        if wireguard:
            await self.client.update(
                "/interface/wireguard/peers", wireguard[".id"], **wireguard_values
            )
        else:
            await self.client.add("/interface/wireguard/peers", **wireguard_values)

        bgp_values = _bgp_values(peer_info)
        bgp = await self._find("/routing/bgp/connection", name=name)
        if bgp:
            await self.client.update("/routing/bgp/connection", bgp[".id"], **bgp_values)
        else:
            await self.client.add("/routing/bgp/connection", **bgp_values)

    async def remove(self, asn):
        name = peer_name(asn)
        wireguard = await self._find("/interface/wireguard/peers", comment=name)
        if wireguard:
            await self.client.remove("/interface/wireguard/peers", wireguard[".id"])
        bgp = await self._find("/routing/bgp/connection", name=name)
        if bgp:
            await self.client.remove("/routing/bgp/connection", bgp[".id"])

    async def restart(self, asn):
        name = peer_name(asn)
        bgp = await self._find("/routing/bgp/connection", name=name)
        if bgp:
            await self.client.set("/routing/bgp/connection", bgp[".id"], disabled="yes")
            await self.client.set("/routing/bgp/connection", bgp[".id"], disabled="no")