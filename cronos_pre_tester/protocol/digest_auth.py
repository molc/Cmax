import hashlib


_XML_ESCAPE_TABLE = str.maketrans({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
})


def _xml_escape(s: str) -> str:
    return str(s).translate(_XML_ESCAPE_TABLE)


def calc_digest_response(
    username: str,
    password: str,
    realm: str,
    nonce: str,
    uri: str,
    method: str = "Login",
) -> str:
    """
    根据协议文档计算 HTTP Digest 认证响应值。

    response = md5(md5(user:realm:pwd) : nonce : md5(method:uri))

    淳中: realm="Athena2", method="Login"(中文)/"LoginEN"(英文)
    中性: realm="Athena-ii", method="Register"/"RegisterEn"
    """
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()


def build_login_step1_xml(username: str, user_agent: str = "0", typ: str = "0", lang: str = "1") -> str:
    """构建登录第一步的 XML（无 Authorization）。"""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<Message>"
        f"<UserName>{_xml_escape(username)}</UserName>"
        f"<UserAgent>{user_agent}</UserAgent>"
        f"<Type>{typ}</Type>"
        f"<Language>{lang}</Language>"
        f"</Message>"
    )


def build_login_step2_xml(
    username: str,
    realm: str,
    nonce: str,
    uri: str,
    password: str,
    user_agent: str = "0",
    typ: str = "0",
    lang: str = "1",
) -> str:
    """构建登录第二步的 XML（带 Authorization/Digest）。"""
    response = calc_digest_response(username, password, realm, nonce, uri)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<Message>"
        f"<UserName>{_xml_escape(username)}</UserName>"
        f"<UserAgent>{user_agent}</UserAgent>"
        f"<Type>{typ}</Type>"
        f"<Language>{lang}</Language>"
        f"<Authorization method=\"Digest\">"
        f"<realm>{_xml_escape(realm)}</realm>"
        f"<nonce>{nonce}</nonce>"
        f"<uri>{uri}</uri>"
        f"<response>{response}</response>"
        f"</Authorization>"
        f"</Message>"
    )
