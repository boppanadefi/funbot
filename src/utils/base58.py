"""
Base58 Encoding & Decoding (Bitcoin / Solana alphabet).
Zero external dependencies.
"""

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    """Encodes bytes into a Base58 string."""
    n = int.from_bytes(b, "big")
    res = []
    while n > 0:
        n, r = divmod(n, 58)
        res.append(B58_ALPHABET[r])
    res = "".join(reversed(res))
    pad = 0
    for byte in b:
        if byte == 0:
            pad += 1
        else:
            break
    return "1" * pad + res


def b58decode(s: str) -> bytes:
    """Decodes a Base58 string into bytes."""
    n = 0
    for char in s:
        if char not in B58_ALPHABET:
            raise ValueError(f"Invalid Base58 character: '{char}'")
        n = n * 58 + B58_ALPHABET.index(char)
    res = []
    while n > 0:
        n, r = divmod(n, 256)
        res.append(r)
    pad = 0
    for char in s:
        if char == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + bytes(reversed(res))
