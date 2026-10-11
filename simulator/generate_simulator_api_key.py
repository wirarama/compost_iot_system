import secrets
import hashlib 

key = secrets.token_hex(32)
key_hash = hashlib.sha256(key.encode()).hexdigest()

print(f"Key: {key}")
print(f"Key hash: {key_hash}")