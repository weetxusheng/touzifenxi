---
name: chinawealth-scraper
description: Scrape and export ChinaWealth product list data by calling the encrypted `/prod/search` backend, including the RSA/AES key exchange, request signing, pagination, and click-word captcha retries. Use when an AI agent needs to fetch wealth-management product listings from chinawealth.com.cn, rerun exports with different `prodStatus` values, troubleshoot secondary verification failures, or explain how the protocol works.
---

# ChinaWealth Scraper

## Overview

Use the bundled script to collect list data from `https://www.chinawealth.com.cn/lcweb/management/proScreen` and export rows to CSV.

Read [references/protocol.md](references/protocol.md) only when the endpoint, signature, encryption, or captcha behavior changes.

## Quick Start

1. Run a short verification fetch first from the skill root or by replacing `<skill-root>` with the installed directory.

```powershell
python "<skill-root>\\scripts\\chinawealth_scraper.py" `
  --max-pages 1 `
  --page-size 20 `
  --output ".\chinawealth_check.csv"
```

2. Inspect the printed `total rows` and confirm the CSV header looks correct.
3. Run a full export without `--max-pages` after the probe succeeds.

## Workflow

1. Keep the encrypted request flow intact. The backend rejects plain JSON submissions.
2. Adjust filters through CLI flags before patching the script. Expose new flags only after confirming the browser payload has changed.
3. Leave `--sleep-seconds` conservative unless the user explicitly wants a faster run. The site can return `429` or trigger the secondary-verification marker `\u4e8c\u6b21\u6821\u9a8c`.
4. Compare the exported row count with the API `total`. If they differ on a full run, retry the missing pages before changing parsing logic.

## Captcha Handling

- Let the script attempt OCR automatically when the API message contains the marker `\u4e8c\u6b21\u6821\u9a8c`.
- Provide OCR dependencies through the current Python environment or a `_vendor` directory. The script searches `CHINAWEALTH_VENDOR_DIR`, `scripts/_vendor`, the skill root `_vendor`, and `./_vendor`.
- If captcha solving fails repeatedly, inspect the current captcha images and then read [references/protocol.md](references/protocol.md) before changing the solver.

## Resources

- `scripts/chinawealth_scraper.py`: Run the end-to-end scraper and CSV exporter.
- `references/protocol.md`: Check the endpoint map, signing notes, and maintenance checklist.
