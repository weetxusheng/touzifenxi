# ChinaWealth Protocol Reference

## Target Surface

- List page: `https://www.chinawealth.com.cn/lcweb/management/proScreen`
- API base: `https://www.chinawealth.com.cn/lcw-fe-service`
- Default list filter in the bundled script: `prodStatus=02`, `prodCollectMeth=01,NA`

## Endpoint Sequence

1. `POST /m/n`
   Send a generated RSA public key as plain text.
   Receive a base64 blob that decrypts to the current AES management key.
2. `POST /prod/search`
   Send the compact JSON payload encrypted with AES-ECB and sign the request with `X-Nonce`, `X-Timestamp`, and `X-Sign`.
3. `POST /captcha/getCaptcha`
   Request a click-word captcha after the API asks for secondary verification.
4. `POST /captcha/checkCaptcha`
   Test encrypted click coordinates until the site accepts one combination.

## Encryption And Signing

- Generate a fresh 1024-bit RSA keypair per search attempt.
- Strip the PEM header and footer before posting the public key to `/m/n`.
- Treat the decrypted `/m/n` response as a base64-encoded AES key.
- Serialize payloads with compact JSON separators before encrypting them.
- Compute the search signature as:

```text
sha256("data=<encrypted>&nonce=<nonce>&timestamp=<ms>&signKey=hold?fish:palm")
```

- Send the signature in `X-Sign`.

## Captcha Flow

- Trigger condition: non-200 API response message containing the marker `\u4e8c\u6b21\u6821\u9a8c`.
- `getCaptcha` returns `wordList`, `originalImageBase64`, `secretKey`, and `token`.
- The solver upsamples the image, runs `cnocr` with the target characters, and supplements OCR with HSV color masks plus connected-component centroids.
- Each candidate coordinate set is encrypted with AES-ECB using the raw UTF-8 `secretKey`.
- On success, build `token---pointJson`, encrypt it with the same `secretKey`, and resubmit it as `captchaVerification` in `/prod/search`.

## Maintenance Checklist

1. Confirm the browser still calls `/m/n`, `/prod/search`, `/captcha/getCaptcha`, and `/captcha/checkCaptcha`.
2. Re-check the `X-Sign` formula and the constant sign key if the API starts rejecting valid payloads.
3. Compare `build_payload()` with the browser network payload before adding or removing fields.
4. Inspect new captcha screenshots before changing OCR thresholds or fallback heuristics.
