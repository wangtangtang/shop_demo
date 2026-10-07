"""安全工具：密码哈希存储 + 签名 Token 签发/校验（零第三方依赖，仅标准库）。

本模块取代早期 demo 的两个不安全设计：
  1. 密码【明文存储】→ 改为 PBKDF2-SHA256 加盐哈希（拖库也无法还原密码）；
  2. Token【直接等于 user_id，可伪造】→ 改为 HMAC-SHA256 签名的限时 token，
     无状态、篡改即验签失败，不知道密钥无法冒充任何用户。

纯工具函数，不依赖 flask（读取密钥/有效期通过参数传入），
方便在脚本迁移、单元测试中直接调用。
"""
import base64
import hashlib
import hmac
import os
import time

# ---- 密码哈希（PBKDF2-SHA256）----
_HASH_ALGO = "sha256"
_HASH_ITER = 260000          # 迭代次数，OWASP 推荐量级
_SALT_BYTES = 16


def _b64(raw):
    return base64.b64encode(raw).decode("ascii").strip()


def _unb64(text):
    return base64.b64decode(text.encode("ascii"))


def hash_password(password):
    """明文密码 → 加盐哈希字符串。
    格式 pbkdf2:sha256:<iter>$<salt_b64>$<hash_b64>，与 werkzeug 风格一致。"""
    if not isinstance(password, str):
        password = str(password)
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode("utf-8"),
                             salt, _HASH_ITER)
    return f"pbkdf2:{_HASH_ALGO}:{_HASH_ITER}${_b64(salt)}${_b64(dk)}"


def verify_password(password, stored):
    """校验明文密码与哈希串是否匹配（常数时间比较，防计时攻击）。
    stored 不是合法哈希格式时直接返回 False。"""
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    try:
        head, salt_b64, hash_b64 = stored.split("$")
        algo_part = head.split(":")          # ["pbkdf2","sha256","260000"]
        if algo_part[0] != "pbkdf2":
            return False
        algo = algo_part[1]
        iterations = int(algo_part[2])
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except (ValueError, IndexError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def is_hashed(stored):
    """判断库里的密码是否已是哈希格式（迁移脚本幂等用）。"""
    return isinstance(stored, str) and stored.startswith("pbkdf2:")


# ---- 签名 Token（HMAC-SHA256，限时）----
_TOKEN_TTL_SECONDS = 7 * 24 * 3600   # 默认 7 天


def _sign(payload_b64, secret):
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"),
                    hashlib.sha256).hexdigest()


def issue_token(user_id, secret, ttl_seconds=_TOKEN_TTL_SECONDS):
    """为用户签发限时签名 token。
    结构：<payload_b64url>.<sig_hex>，payload = "uid.expire_unix"。"""
    expire = int(time.time()) + int(ttl_seconds)
    payload = f"{int(user_id)}.{expire}"
    p = base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")
    sig = _sign(p, secret)
    return f"{p}.{sig}"


def parse_token(token, secret):
    """校验 token，通过返回 user_id(int)；任何篡改/过期/格式错返回 None。"""
    if not isinstance(token, str) or "." not in token:
        return None
    p, sig = token.rsplit(".", 1)
    # 1) 验签：签名必须一致（防篡改、防伪造）
    expected = _sign(p, secret)
    if not hmac.compare_digest(sig, expected):
        return None
    # 2) 解 payload
    try:
        pad = "=" * (-len(p) % 4)
        payload = base64.urlsafe_b64decode(p + pad).decode("ascii")
        uid_s, expire_s = payload.split(".")
        uid = int(uid_s)
        expire = int(expire_s)
    except (ValueError, base64.binascii.Error):
        return None
    # 3) 过期检查
    if time.time() > expire:
        return None
    return uid
