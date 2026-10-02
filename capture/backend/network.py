"""本机代理地址展示，不发送探测请求或依赖外部服务。"""

import socket


def lan_ip():
    """通过系统路由选择 IPv4 地址；UDP connect 只选路，不发送数据。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
            channel.connect(("192.0.2.1", 9))
            address = channel.getsockname()[0]
            if address and address != "0.0.0.0":
                return address
    except OSError:
        pass
    try:
        return next(
            address[4][0]
            for address in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET
            )
            if not address[4][0].startswith("127.")
        )
    except (OSError, StopIteration):
        return None


def proxy_addresses(settings):
    """返回实际端口、监听范围和当前路由 IP；离线时不显示虚构地址。"""
    address = lan_ip()
    port = settings.listen_port
    return {
        "lan_ip": address,
        "lan_address": f"{address}:{port}" if address else None,
        "listen_address": f"{settings.listen_host}:{port}",
        "lan_accessible": settings.listen_host == "0.0.0.0" and address is not None,
    }
