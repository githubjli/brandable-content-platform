# JWT Keys

Place your RSA key pair here:
- `jwt_private.pem` — RSA private key (NEVER commit to git)
- `jwt_public.pem` — RSA public key (safe to commit)

Generate a dev key pair:
```bash
openssl genrsa -out django/config/keys/jwt_private.pem 2048
openssl rsa -in django/config/keys/jwt_private.pem -pubout -out django/config/keys/jwt_public.pem
```
