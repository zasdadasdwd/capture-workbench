"""公开 CA 下载与安装说明；绝不暴露私钥。"""

import hashlib

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from capture.backend.documents import certificate_page
from config import DATA, ROOT

router = APIRouter()


@router.get("/api/certificate")
def certificate():
    """只提供公开 CA 证书，不暴露包含私钥的 mitmproxy-ca.pem。"""
    path = DATA / "certificates/mitmproxy-ca-cert.pem"
    if not path.exists():
        raise HTTPException(404, "请先启动一次抓包，让引擎生成证书")
    return FileResponse(
        path, filename="capture-ca.pem", media_type="application/x-pem-file"
    )


@router.get("/api/certificate/info")
def certificate_info():
    """返回 CA 的 DER SHA-256 指纹，便于确认安装的是本实例证书。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    path = DATA / "certificates/mitmproxy-ca-cert.pem"
    if not path.exists():
        return {"available": False}
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    fingerprint = hashlib.sha256(
        certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    return {"available": True, "sha256": fingerprint}


@router.get("/docs/certificate")
def certificate_document():
    """渲染安装说明，命令和代码可逐行复制，也可复制整段。"""
    return HTMLResponse(certificate_page())


@router.get("/docs/certificate/source")
def certificate_source():
    """继续提供原始 Markdown，便于分享或离线阅读。"""
    return FileResponse(
        ROOT / "capture/support/docs/证书安装.md",
        filename="证书安装.md",
        media_type="text/markdown; charset=utf-8",
    )
