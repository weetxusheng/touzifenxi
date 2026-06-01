import argparse
import base64
import csv
import itertools
import json
import math
import os
import sys
import time
import uuid
from io import BytesIO
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent


def discover_vendor_dirs() -> list[Path]:
    candidates = [
        os.environ.get("CHINAWEALTH_VENDOR_DIR"),
        SCRIPT_DIR / "_vendor",
        SKILL_DIR / "_vendor",
        Path.cwd() / "_vendor",
    ]
    discovered: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        discovered.append(path)
    return discovered


VENDOR_DIRS = discover_vendor_dirs()
for vendor_dir in VENDOR_DIRS:
    if vendor_dir.exists():
        vendor_path = str(vendor_dir)
        if vendor_path not in sys.path:
            sys.path.insert(0, vendor_path)


import requests
from cryptography.hazmat.primitives import hashes, padding as sympadding, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://www.chinawealth.com.cn/lcw-fe-service"
LIST_PAGE_URL = "https://www.chinawealth.com.cn/lcweb/management/proScreen"
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "*/*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.chinawealth.com.cn",
    "Referer": LIST_PAGE_URL,
}
SIGN_KEY = "hold?fish:palm"
CAPTCHA_REQUIRED_MARKER = "\u4e8c\u6b21\u6821\u9a8c"


def sha256_hex(value: str) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(value.encode("utf-8"))
    return digest.finalize().hex()


def aes_ecb_encrypt(plaintext: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    padder = sympadding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def aes_ecb_encrypt_text_key(plaintext: str, key_text: str) -> str:
    key = key_text.encode("utf-8")
    padder = sympadding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def compact_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def build_payload(page: int, page_size: int, prod_status: str) -> dict:
    return {
        "searchType": "",
        "quickQueryWords": "",
        "currentPage": page,
        "pageSize": page_size,
        "prodRegCode": "",
        "prodName": "",
        "orgName": "",
        "prodStatus": prod_status,
        "orgTypeList": [],
        "prodCollectMeth": "01,NA",
        "prodSpclAttrList": [],
        "prodOperateModeList": [],
        "prodInvestNatureList": [],
        "prodRiskLevelList": [],
        "prodTermCodeList": [],
        "collCCYList": [],
        "performanceCompareBaseCap": "",
        "performanceCompareBaseFloor": "",
        "prodSaleZone": "",
        "orderConfig": "",
    }


class CaptchaSolver:
    def __init__(self, max_captcha_rounds: int, max_combinations: int):
        self.max_captcha_rounds = max_captcha_rounds
        self.max_combinations = max_combinations
        self._ocr_cache: dict[str, object] = {}
        self._deps_loaded = False

    def _load_deps(self) -> None:
        if self._deps_loaded:
            return
        try:
            from PIL import Image, ImageDraw
            import cv2
            import numpy as np
            from cnocr import CnOcr
        except ImportError as exc:
            searched = ", ".join(str(path) for path in VENDOR_DIRS) or "<none>"
            raise RuntimeError(
                "captcha OCR dependencies are missing. Install Pillow, opencv-python, numpy, "
                "and cnocr in the current interpreter or point CHINAWEALTH_VENDOR_DIR to a "
                f"compatible _vendor directory. Searched: {searched}"
            ) from exc

        self.Image = Image
        self.ImageDraw = ImageDraw
        self.cv2 = cv2
        self.np = np
        self.CnOcr = CnOcr
        self._deps_loaded = True

    def _get_ocr(self, targets: list[str]):
        key = "".join(targets)
        if key not in self._ocr_cache:
            self._ocr_cache[key] = self.CnOcr(
                rec_model_name="scene-densenet_lite_136-gru",
                cand_alphabet=key,
            )
        return self._ocr_cache[key]

    @staticmethod
    def _split_points(box: list[list[float]], count: int) -> list[tuple[float, float]]:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max_x - min_x
        height = max_y - min_y
        points: list[tuple[float, float]] = []
        if width >= height:
            step = width / max(count, 1)
            center_y = sum(ys) / len(ys)
            for index in range(count):
                points.append((min_x + step * (index + 0.5), center_y))
        else:
            step = height / max(count, 1)
            center_x = sum(xs) / len(xs)
            for index in range(count):
                points.append((center_x, min_y + step * (index + 0.5)))
        return points

    @staticmethod
    def _clip_point(x: float, y: float) -> dict:
        return {
            "x": max(1, min(309, int(round(x)))),
            "y": max(1, min(154, int(round(y)))),
        }

    def _add_candidate(self, store: list[dict], point: tuple[float, float], score: float, label: str) -> None:
        clipped = self._clip_point(point[0], point[1])
        store.append(
            {
                "x": clipped["x"],
                "y": clipped["y"],
                "score": float(score),
                "label": label,
            }
        )

    @staticmethod
    def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
        sorted_candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)
        deduped: list[dict] = []
        for candidate in sorted_candidates:
            if all(
                abs(candidate["x"] - existing["x"]) > 10
                or abs(candidate["y"] - existing["y"]) > 10
                for existing in deduped
            ):
                deduped.append(candidate)
        return deduped

    def _scene_candidates(self, original_image, targets: list[str]) -> tuple[dict[str, list[dict]], list[dict]]:
        upscaled = original_image.resize((original_image.width * 4, original_image.height * 4))
        draw = self.ImageDraw.Draw(upscaled)
        draw.rectangle((900, 500, 1240, 620), fill="white")

        ocr = self._get_ocr(targets)
        results = ocr.ocr(self.np.array(upscaled))
        labeled = {target: [] for target in targets}
        generic: list[dict] = []
        for item in results:
            text = "".join(char for char in item["text"] if char.strip())
            box = item["position"]
            points = self._split_points(box, max(1, len(text)))
            if text:
                for index, char in enumerate(text):
                    point = points[min(index, len(points) - 1)]
                    point = (point[0] / 4, point[1] / 4)
                    if char in labeled:
                        self._add_candidate(labeled[char], point, item["score"], "ocr")
                    self._add_candidate(generic, point, item["score"], "ocr_any")
            else:
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                self._add_candidate(
                    generic,
                    (sum(xs) / len(xs) / 4, sum(ys) / len(ys) / 4),
                    item["score"] * 0.6,
                    "ocr_blank",
                )
        return labeled, generic

    def _component_candidates(self, original_image, generic: list[dict]) -> None:
        bgr = self.cv2.cvtColor(self.np.array(original_image), self.cv2.COLOR_RGB2BGR)
        hsv = self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2HSV)
        masks = [
            (((hsv[:, :, 1] > 35) & (hsv[:, :, 0] >= 110) & (hsv[:, :, 0] <= 155)).astype("uint8") * 255),
            (
                (
                    ((hsv[:, :, 0] <= 25) | (hsv[:, :, 0] >= 170))
                    & (hsv[:, :, 1] > 8)
                    & (hsv[:, :, 2] > 235)
                ).astype("uint8")
                * 255
            ),
            (
                (
                    (hsv[:, :, 1] < 40)
                    & (hsv[:, :, 2] < 235)
                    & (hsv[:, :, 2] > 120)
                ).astype("uint8")
                * 255
            ),
        ]
        kernel = self.np.ones((3, 3), self.np.uint8)
        for mask in masks:
            dilated = self.cv2.dilate(mask, kernel, iterations=1)
            count, _, stats, centroids = self.cv2.connectedComponentsWithStats(dilated, 8)
            for index in range(1, count):
                x, y, width, height, area = stats[index]
                if area < 35 or width < 8 or height < 8 or width > 120 or height > 120:
                    continue
                if x > original_image.width - 90 and y > original_image.height - 40:
                    continue
                self._add_candidate(generic, centroids[index], 0.2, "component")

    def _build_candidates(self, image_bytes: bytes, targets: list[str]) -> tuple[dict[str, list[dict]], list[dict]]:
        original_image = self.Image.open(BytesIO(image_bytes)).convert("RGB")
        labeled, generic = self._scene_candidates(original_image, targets)
        self._component_candidates(original_image, generic)
        labeled = {target: self._dedupe_candidates(items) for target, items in labeled.items()}
        generic = self._dedupe_candidates(generic)
        return labeled, generic

    def _candidate_lists(self, targets: list[str], labeled: dict[str, list[dict]], generic: list[dict]) -> list[list[dict]]:
        per_target: list[list[dict]] = []
        for target in targets:
            candidates = list(labeled[target])
            for generic_item in generic:
                if all(
                    abs(generic_item["x"] - candidate["x"]) > 8
                    or abs(generic_item["y"] - candidate["y"]) > 8
                    for candidate in candidates
                ):
                    candidates.append(generic_item)
                if len(candidates) >= 5:
                    break
            per_target.append(candidates[:5] if candidates else generic[:5])
        return per_target

    def solve(self, session: requests.Session) -> str | None:
        self._load_deps()
        for round_index in range(1, self.max_captcha_rounds + 1):
            client_uid = str(uuid.uuid4())
            response = session.post(
                f"{BASE_URL}/captcha/getCaptcha",
                json={"captchaType": "clickWord", "clientUid": client_uid},
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()["repData"]
            targets = payload["wordList"]
            image_bytes = base64.b64decode(payload["originalImageBase64"])
            labeled, generic = self._build_candidates(image_bytes, targets)
            per_target = self._candidate_lists(targets, labeled, generic)

            attempts = 0
            for combo in itertools.product(*per_target):
                points = [{"x": item["x"], "y": item["y"]} for item in combo]
                if len({(point["x"], point["y"]) for point in points}) < len(points):
                    continue
                attempts += 1
                point_payload = compact_json(points)
                point_json = aes_ecb_encrypt_text_key(point_payload, payload["secretKey"])
                verify_response = session.post(
                    f"{BASE_URL}/captcha/checkCaptcha",
                    json={
                        "captchaType": "clickWord",
                        "pointJson": point_json,
                        "token": payload["token"],
                    },
                    headers=HEADERS,
                    timeout=30,
                )
                verify_response.raise_for_status()
                verify_data = verify_response.json()
                if verify_data.get("repCode") == "0000":
                    verification_payload = f"{payload['token']}---{point_payload}"
                    print(
                        f"captcha solved on round {round_index}, combination {attempts}, "
                        f"targets={targets}"
                    )
                    return aes_ecb_encrypt_text_key(verification_payload, payload["secretKey"])
                if attempts >= self.max_combinations:
                    break
            print(f"captcha round {round_index} failed after {attempts} combinations")
        return None


class ChinaWealthClient:
    def __init__(
        self,
        page_size: int,
        prod_status: str,
        sleep_seconds: float,
        retries: int,
        captcha_rounds: int,
        captcha_combinations: int,
        max_pages: int | None,
    ):
        self.page_size = page_size
        self.prod_status = prod_status
        self.sleep_seconds = sleep_seconds
        self.retries = retries
        self.max_pages = max_pages
        self.captcha_solver = CaptchaSolver(captcha_rounds, captcha_combinations)

    def _make_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=None,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    def _get_manage_key(self, session: requests.Session) -> str:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        public_key = (
            public_pem.replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\n", "")
        )
        response = session.post(
            f"{BASE_URL}/m/n",
            data=public_key,
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        return private_key.decrypt(base64.b64decode(response.text), padding.PKCS1v15()).decode("utf-8")

    def _perform_search(
        self,
        session: requests.Session,
        manage_key: str,
        page: int,
        captcha_verification: str | None = None,
    ) -> dict:
        payload = build_payload(page, self.page_size, self.prod_status)
        if captcha_verification:
            payload["captchaVerification"] = captcha_verification
        payload_text = compact_json(payload)
        encrypted_data = aes_ecb_encrypt(payload_text, manage_key)
        nonce = uuid.uuid4().hex[:10]
        timestamp = str(int(time.time() * 1000))
        sign = sha256_hex(
            f"data={encrypted_data}&nonce={nonce}&timestamp={timestamp}&signKey={SIGN_KEY}"
        )
        response = session.post(
            f"{BASE_URL}/prod/search",
            data=encrypted_data,
            headers={
                **HEADERS,
                "X-Nonce": nonce,
                "X-Timestamp": timestamp,
                "X-Sign": sign,
            },
            timeout=30,
        )
        if response.status_code == 429:
            raise requests.HTTPError("429 Too Many Requests", response=response)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("application/json"):
            raise RuntimeError(f"unexpected response type: {response.headers.get('content-type', '')}")
        return response.json()

    def _search_once(self, page: int) -> dict:
        session = self._make_session()
        manage_key = self._get_manage_key(session)
        data = self._perform_search(session, manage_key, page)
        if data.get("code") != 200 and CAPTCHA_REQUIRED_MARKER in data.get("msg", ""):
            print(f"page {page}: captcha required, attempting OCR solve")
            captcha_verification = self.captcha_solver.solve(session)
            if not captcha_verification:
                raise RuntimeError(f"search failed: {data.get('msg', 'captcha solve failed')}")
            data = self._perform_search(session, manage_key, page, captcha_verification)
        if data.get("code") != 200:
            raise RuntimeError(f"search failed: {data.get('msg', 'unknown error')}")
        return data["data"]

    def search_page(self, page: int) -> dict:
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                return self._search_once(page)
            except Exception as exc:
                last_error = exc
                if attempt == self.retries:
                    break
                time.sleep(max(self.sleep_seconds, 2.0) * attempt)
        raise RuntimeError(f"page {page} failed after {self.retries} attempts") from last_error

    def fetch_all(self) -> list[dict]:
        first_page = self.search_page(1)
        rows = list(first_page["data"])
        total = first_page["total"]
        full_total_pages = math.ceil(total / self.page_size)
        total_pages = full_total_pages
        if self.max_pages is not None:
            total_pages = min(total_pages, self.max_pages)
        print(f"total rows: {total}, total pages: {total_pages}")

        for page in range(2, total_pages + 1):
            time.sleep(self.sleep_seconds)
            page_data = self.search_page(page)
            rows.extend(page_data["data"])
            print(f"fetched page {page}/{total_pages}, accumulated rows: {len(rows)}")

        if self.max_pages is None and len(rows) != total:
            print(f"warning: expected {total} rows, got {len(rows)}")
        if self.max_pages is not None and total_pages < full_total_pages:
            print(
                f"partial run: fetched {len(rows)} rows from {total_pages}/{full_total_pages} pages"
            )
        return rows


def collect_fieldnames(rows: list[dict]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def write_csv(rows: list[dict], output_path: Path) -> None:
    fieldnames = collect_fieldnames(rows)
    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape ChinaWealth proScreen list results to CSV.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"chinawealth_proscreen_prodstatus02_{time.strftime('%Y%m%d')}.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size for list requests. Default: 100.",
    )
    parser.add_argument(
        "--prod-status",
        default="02",
        help="Product status filter. The list page default is 02 (fundraising).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=4.0,
        help="Delay between pages in seconds.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retry count per page.",
    )
    parser.add_argument(
        "--captcha-rounds",
        type=int,
        default=8,
        help="How many fresh captchas to try when OCR solving is needed.",
    )
    parser.add_argument(
        "--captcha-combinations",
        type=int,
        default=80,
        help="How many point combinations to test per captcha.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional upper bound for pages to fetch. Useful for verification runs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = ChinaWealthClient(
        page_size=args.page_size,
        prod_status=args.prod_status,
        sleep_seconds=args.sleep_seconds,
        retries=args.retries,
        captcha_rounds=args.captcha_rounds,
        captcha_combinations=args.captcha_combinations,
        max_pages=args.max_pages,
    )
    rows = client.fetch_all()
    output_path = args.output.resolve()
    write_csv(rows, output_path)
    print(f"saved {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
