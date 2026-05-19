import hashlib
import logging
import random
import re
import threading
import time
import urllib.parse
import zlib

import requests

from .base import CloudProvider
from .registry import register


class Pan123Provider(CloudProvider):
    name = "123pan"
    label = "123云盘"
    link_type = "123pan"
    auth_type = "password"
    config_keys = ["123pan_username", "123pan_password"]
    supports_subscription = True
    supports_offline = False
    supports_fixed_share_link = True
    supports_rename = True
    supports_move = True
    supports_copy = False
    supports_delete = True
    drive_id = 0
    app_version = "3"
    platform = "web"
    rate_limit_seconds = 0.35
    api_hosts = ("www.123pan.com", "www.123684.com", "www.123865.com", "www.123912.com")
    browser_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    share_url_pattern = re.compile(
        r"(?:https?://)?(?:www\.)?(?:123pan|123684|123865|123912)\.(?:com|cn)/s/([^/?#\s<>'\"]+)",
        re.IGNORECASE,
    )

    def __init__(self):
        super().__init__()
        self._auth_token = None
        self._auth_lock = threading.Lock()
        self._http_local = threading.local()

    def _get_session(self) -> requests.Session:
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            self._http_local.session = session
        return session

    def _reset_session(self) -> None:
        session = getattr(self._http_local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self._http_local.session = requests.Session()

    def _credential_cache_key(self, username: str, password: str) -> str:
        password_fingerprint = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return f"{username}|{password_fingerprint}"

    def _sign_path(self, path: str) -> tuple:
        table = ["a", "d", "e", "f", "g", "h", "l", "m", "y", "i", "j", "n", "o", "p", "k", "q", "r", "s", "t", "u", "b", "c", "v", "w", "s", "z"]
        random_value = str(round(1e7 * random.random()))
        now = time.time()
        timestamp = str(int(now))
        cst = time.gmtime(now + 8 * 3600)
        now_text = time.strftime("%Y%m%d%H%M", cst)
        mapped_time = "".join(table[int(ch)] for ch in now_text)
        time_sign = str(zlib.crc32(mapped_time.encode("utf-8")) & 0xFFFFFFFF)
        data = "|".join([timestamp, random_value, str(path or ""), self.platform, self.app_version, time_sign])
        data_sign = str(zlib.crc32(data.encode("utf-8")) & 0xFFFFFFFF)
        return time_sign, "-".join([timestamp, random_value, data_sign])

    def _signed_api_url(self, raw_url: str) -> str:
        parsed = urllib.parse.urlparse(str(raw_url or ""))
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(self._sign_path(parsed.path))
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))

    def _api_url_candidates(self, raw_url: str) -> list:
        parsed = urllib.parse.urlparse(str(raw_url or ""))
        host = str(parsed.hostname or "").lower()
        if host not in self.api_hosts:
            return [raw_url]
        candidates = []
        for candidate_host in self.api_hosts:
            if parsed.port:
                netloc = f"{candidate_host}:{parsed.port}"
            else:
                netloc = candidate_host
            candidate = urllib.parse.urlunparse(parsed._replace(netloc=netloc))
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _is_retryable_http_error(self, exc: Exception) -> bool:
        if not isinstance(exc, requests.exceptions.HTTPError):
            return False
        response = getattr(exc, "response", None)
        status_code = int(getattr(response, "status_code", 0) or 0)
        return status_code in (408, 409, 425, 429, 500, 502, 503, 504)

    def _is_retryable_request_error(self, exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
            ),
        )

    def _format_transport_error(self, exc: Exception) -> str:
        if isinstance(exc, requests.exceptions.HTTPError):
            response = getattr(exc, "response", None)
            status_code = int(getattr(response, "status_code", 0) or 0)
            detail = ""
            try:
                data = response.json() if response is not None else {}
                if isinstance(data, dict):
                    detail = str(data.get("message", "") or data.get("msg", "") or data.get("error", "") or "").strip()
            except Exception:
                detail = ""
            message = f"HTTP {status_code}" if status_code else "HTTP 请求失败"
            return f"{message}: {detail}" if detail else message
        message = str(exc or "").strip()
        lowered = message.lower()
        if isinstance(exc, requests.exceptions.SSLError) or "ssl" in lowered or "eof occurred in violation of protocol" in lowered:
            return "上游提前断开 TLS 连接，可能是 123 云盘临时风控、线路波动或当前网络出口被拒绝"
        if isinstance(exc, requests.exceptions.Timeout) or "timeout" in lowered or "timed out" in lowered:
            return "请求超时，可能是 123 云盘接口繁忙或当前网络出口不稳定"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "连接失败，可能是 123 云盘接口繁忙或当前网络出口不稳定"
        return message or "网络请求失败"

    def _raise_transport_error(self, exc: Exception, action: str, *, used_fallback_hosts: bool = True) -> None:
        detail = self._format_transport_error(exc)
        retry_tail = "；已自动重试并切换备用域名，请稍后再试" if used_fallback_hosts else "；请稍后再试"
        raise RuntimeError(f"123云盘{action}失败：{detail}{retry_tail}") from exc

    def _request_response(self, method: str, url: str, action: str, *, use_api_hosts: bool = True, **kwargs):
        candidates = self._api_url_candidates(url) if use_api_hosts else [url]
        used_fallback_hosts = use_api_hosts and len(candidates) > 1
        last_exc = None
        for index, candidate_url in enumerate(candidates):
            signed_url = self._signed_api_url(candidate_url) if use_api_hosts else candidate_url
            try:
                response = self._get_session().request(
                    str(method or "GET").upper(),
                    signed_url,
                    **kwargs,
                )
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as exc:
                if not self._is_retryable_http_error(exc):
                    self._raise_transport_error(exc, action, used_fallback_hosts=used_fallback_hosts)
                last_exc = exc
            except requests.exceptions.RequestException as exc:
                if not self._is_retryable_request_error(exc):
                    self._raise_transport_error(exc, action, used_fallback_hosts=used_fallback_hosts)
                last_exc = exc

            if index >= len(candidates) - 1:
                break
            parsed = urllib.parse.urlparse(candidate_url)
            logging.warning(
                "123pan %s transient failure on %s%s: %s; retrying with fallback host",
                action,
                parsed.netloc,
                parsed.path,
                self._format_transport_error(last_exc),
            )
            self._reset_session()
            time.sleep(min(2.0, 0.35 * (index + 1)))
        self._raise_transport_error(
            last_exc or RuntimeError("请求失败"),
            action,
            used_fallback_hosts=used_fallback_hosts,
        )

    def _extract_auth_token(self, payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""
        data = payload.get("data")
        sources = [data, payload] if isinstance(data, dict) else [payload]
        for source in sources:
            for key in ("token", "Token", "accessToken", "access_token", "loginToken", "jwt"):
                token = str(source.get(key, "") or "").strip()
                if token:
                    if token.lower().startswith("bearer "):
                        token = token[7:].strip()
                    return token
        return ""

    def _extract_login_uuid(self, payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""
        data = payload.get("data")
        sources = [data, payload] if isinstance(data, dict) else [payload]
        for source in sources:
            for key in ("LoginUuid", "loginUuid", "login_uuid", "uuid", "UUID"):
                value = str(source.get(key, "") or "").strip()
                if value:
                    return value
        return ""

    def _response_code(self, payload: dict) -> int:
        if not isinstance(payload, dict):
            return -1
        try:
            return int(payload.get("code", -1))
        except (TypeError, ValueError):
            return -1

    def _response_data(self, payload: dict) -> dict:
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        return data if isinstance(data, dict) else {}

    def _extract_item_list(self, payload: dict) -> list:
        data = self._response_data(payload)
        for key in ("infoList", "InfoList", "fileList", "FileList", "list", "List"):
            items = data.get(key)
            if isinstance(items, list):
                return items
        return []

    def _extract_next_marker(self, payload: dict) -> str:
        data = self._response_data(payload)
        for key in ("next", "Next"):
            value = data.get(key)
            if value is not None:
                return str(value)
        return ""

    def _item_value(self, item: dict, *keys, default=""):
        if not isinstance(item, dict):
            return default
        for key in keys:
            if key in item and item.get(key) is not None:
                return item.get(key)
        return default

    def _int_or_zero(self, value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _require_positive_int_id(self, value, action: str) -> int:
        try:
            numeric_id = int(str(value or "").strip())
        except (TypeError, ValueError):
            numeric_id = 0
        if numeric_id <= 0:
            raise RuntimeError(f"123云盘{action}失败：文件 ID 无效")
        return numeric_id

    def _normalize_entry_ids(self, entry_ids, action: str) -> list:
        ids = [self._require_positive_int_id(item, action) for item in (entry_ids or [])]
        if not ids:
            raise RuntimeError(f"123云盘{action}失败：请选择要操作的文件")
        return ids

    def _is_folder_item(self, item: dict) -> bool:
        raw_type = self._item_value(item, "type", "Type", "fileType", "FileType", default=0)
        try:
            return int(raw_type or 0) == 1
        except (TypeError, ValueError):
            return str(raw_type or "").strip().lower() in {"folder", "dir", "directory"}

    def _normalize_entry(self, item: dict, cid: str) -> dict:
        item_id = str(self._item_value(item, "fileId", "fileID", "FileId", "FileID", "FileIdStr", "id", "Id", default="") or "")
        name = str(self._item_value(item, "fileName", "FileName", "filename", "Filename", "name", "Name", default="") or "")
        is_dir = self._is_folder_item(item)
        try:
            size = int(self._item_value(item, "size", "Size", "fileSize", "FileSize", default=0) or 0)
        except (TypeError, ValueError):
            size = 0
        return {
            "id": item_id,
            "name": name,
            "type": "folder" if is_dir else "file",
            "is_dir": is_dir,
            "cid": item_id if is_dir else "",
            "fid": "" if is_dir else item_id,
            "size": size,
            "parent_id": cid or "0",
            "etag": str(self._item_value(item, "etag", "Etag", "ETag", default="") or ""),
            "s3key_flag": str(self._item_value(item, "s3keyFlag", "S3KeyFlag", default="") or ""),
            "drive_id": str(self._item_value(item, "driveId", "DriveId", "driveID", "DriveID", default=self.drive_id) or self.drive_id),
        }

    def _build_trash_file_info(self, item: dict, action: str = "删除") -> dict:
        file_id = self._require_positive_int_id(
            self._item_value(item, "fileId", "fileID", "FileId", "FileID", "FileIdStr", "id", "Id", default=""),
            action,
        )
        name = str(self._item_value(item, "fileName", "FileName", "filename", "Filename", "name", "Name", default="") or "").strip()
        try:
            size = int(self._item_value(item, "size", "Size", "fileSize", "FileSize", default=0) or 0)
        except (TypeError, ValueError):
            size = 0
        raw_type = self._item_value(item, "type", "Type", "fileType", "FileType", default=None)
        try:
            type_value = int(raw_type)
        except (TypeError, ValueError):
            type_value = 1 if self._is_folder_item(item) else 0

        payload = {
            "FileName": name,
            "Size": size,
            "FileId": file_id,
            "Type": type_value,
            "Etag": str(self._item_value(item, "etag", "Etag", "ETag", default="") or ""),
            "S3KeyFlag": str(self._item_value(item, "s3keyFlag", "S3KeyFlag", default="") or ""),
            "DownloadUrl": str(self._item_value(item, "downloadUrl", "DownloadUrl", default="") or ""),
        }
        update_at = self._item_value(item, "updateAt", "UpdateAt", "updatedAt", "UpdatedAt", "updateTime", "UpdateTime", default=None)
        if update_at is not None and str(update_at).strip():
            payload["UpdateAt"] = update_at
        return payload

    def _extract_created_folder_id(self, payload: dict) -> str:
        data = self._response_data(payload)
        sources = [data]
        for key in ("info", "Info", "file", "File"):
            nested = data.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
        for source in sources:
            for key in ("fileId", "fileID", "FileId", "FileID", "FileIdStr", "dirId", "DirId", "id", "Id"):
                value = source.get(key) if isinstance(source, dict) else ""
                if value is not None and str(value).strip():
                    return str(value).strip()
        return ""

    def _extract_share_key(self, share_url: str) -> str:
        match = self.share_url_pattern.search(str(share_url or "").strip())
        if not match:
            return ""
        key = urllib.parse.unquote(match.group(1)).strip()
        key = re.sub(r"\.html?$", "", key, flags=re.IGNORECASE)
        return key.strip().strip("，。；：！？、,.;!?")

    def _ensure_token(self, cfg: dict) -> str:
        """通过账号密码登录获取 auth token，缓存至过期"""
        now = time.time()
        username = str(cfg.get("123pan_username", "")).strip()
        password = str(cfg.get("123pan_password", "")).strip()
        if not username or not password:
            raise RuntimeError("请先在参数配置中填写 123云盘 账号和密码")
        cache_key = self._credential_cache_key(username, password)

        with self._auth_lock:
            if (
                self._auth_token
                and self._auth_token.get("cache_key") == cache_key
                and now < self._auth_token.get("expires_at", 0) - 300
            ):
                return self._auth_token["token"]

            if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", username):
                login_body = {"mail": username, "password": password, "type": 2}
            else:
                login_body = {"passport": username, "password": password, "remember": True}

            resp = self._request_response(
                "POST",
                "https://login.123pan.com/api/user/sign_in",
                "登录",
                use_api_hosts=False,
                headers={
                    "User-Agent": self.browser_user_agent,
                    "Content-Type": "application/json",
                    "Origin": "https://www.123pan.com",
                    "Referer": "https://www.123pan.com/",
                    "platform": self.platform,
                    "app-version": self.app_version,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                json=login_body,
                timeout=15,
            )
            data = resp.json()
            code = self._response_code(data)
            if code != 0 and code != 200:
                raise RuntimeError(f"123云盘登录失败: {data.get('message', '未知错误')}")

            token = self._extract_auth_token(data)
            if not token:
                raise RuntimeError("123云盘登录失败：未获取到 token")

            self._auth_token = {
                "token": token,
                "login_uuid": self._extract_login_uuid(data),
                "expires_at": now + 86400,
                "cache_key": cache_key,
            }
            return token

    def get_cookie(self, cfg: dict) -> str:
        return self._ensure_token(cfg)

    def _headers(self, token: str) -> dict:
        headers = {
            "Authorization": f"Bearer {token}",
            "platform": self.platform,
            "app-version": self.app_version,
            "User-Agent": self.browser_user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.123pan.com/",
            "Origin": "https://www.123pan.com",
        }
        cached = self._auth_token if isinstance(self._auth_token, dict) else {}
        login_uuid = str(cached.get("login_uuid", "") or "").strip() if cached.get("token") == token else ""
        if login_uuid:
            headers["LoginUuid"] = login_uuid
        return headers

    def _api_call(self, token: str, method: str, url: str, **kwargs) -> dict:
        self.throttle()
        headers = self._headers(token)
        timeout = kwargs.pop("timeout", 30)
        resp = self._request_response(
            method,
            url,
            "请求",
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        try:
            data = resp.json()
        except (ValueError, Exception):
            raise RuntimeError("123云盘返回数据异常")
        code = self._response_code(data)
        if code != 0 and code != 200:
            raise RuntimeError(f"123云盘 API 错误: {data.get('message', '未知错误')}")
        return data

    def _list_entries_api_payload(self, cookie, cid="0") -> dict:
        return self._api_call(
            cookie, "GET",
            "https://www.123pan.com/b/api/file/list/new",
            params={
                "driveId": str(self.drive_id),
                "limit": "100",
                "next": "0",
                "orderBy": "file_id",
                "orderDirection": "desc",
                "parentFileId": str(self._int_or_zero(cid)),
                "trashed": "false",
                "SearchData": "",
                "Page": "1",
                "OnlyLookAbnormalFile": "0",
                "event": "homeListFile",
                "operateType": "4",
                "inDirectSpace": "false",
            },
        )

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        data = self._list_entries_api_payload(cookie, cid)
        entries = []
        items = self._extract_item_list(data)
        for item in items:
            entry = self._normalize_entry(item, cid)
            if folders_only and not entry["is_dir"]:
                continue
            entries.append(entry)
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        payload_data = self._response_data(data)
        total = self._int_or_zero(payload_data.get("total", payload_data.get("Total", len(entries))))
        return {
            "entries": entries,
            "total": total or len(entries),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
        }

    def list_entries(self, cookie, cid="0"):
        return self.list_entries_payload(cookie, cid)["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/upload_request",
            json={
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "etag": "",
                "fileName": folder_name,
                "parentFileId": self._int_or_zero(cid),
                "size": 0,
                "type": 1,
            },
        )
        folder_id = self._extract_created_folder_id(data)
        if not folder_id:
            raise RuntimeError("123云盘创建文件夹失败：未获取到文件夹 ID")
        return {"cid": folder_id, "name": folder_name}

    def resolve_folder_id_by_path(self, cookie, relative_path):
        parts = [p.strip() for p in str(relative_path).split("/") if p.strip()]
        cid = "0"
        for name in parts:
            entries = self.list_entries(cookie, cid)
            found = next((e for e in entries if e.get("name") == name), None)
            if not found:
                return ""
            cid = found["id"]
        return cid

    def ensure_folder_id_by_path(self, cookie, relative_path):
        parts = [p.strip() for p in str(relative_path).split("/") if p.strip()]
        cid = "0"
        for name in parts:
            entries = self.list_entries(cookie, cid)
            found = next((e for e in entries if e.get("name") == name), None)
            if found:
                cid = found["id"]
            else:
                result = self.create_folder(cookie, cid, name)
                cid = result["cid"]
        return cid

    def resolve_share_payload(self, cookie, share_url, raw_text="", receive_code=""):
        share_code = self._extract_share_key(share_url)
        if not share_code:
            raise RuntimeError("无法识别123云盘分享链接")
        return {
            "share_code": share_code,
            "receive_code": str(receive_code or "").strip(),
            "url": str(share_url or "").strip(),
        }

    def list_share_entries(self, cookie, share_payload, cid="0", offset=0, limit=200):
        share_code = share_payload["share_code"]
        receive_code = share_payload.get("receive_code", "")
        normalized_offset = max(0, int(offset or 0))
        normalized_limit = max(1, min(int(limit or 200), 400))
        entries = []
        total_seen = 0
        page = 1
        next_marker = "0"
        total = 0
        share_title = ""
        while len(entries) < normalized_limit:
            data = self._api_call(
                cookie,
                "GET",
                "https://www.123pan.com/b/api/share/get",
                params={
                    "limit": "100",
                    "next": next_marker or "0",
                    "orderBy": "file_id",
                    "orderDirection": "desc",
                    "parentFileId": str(self._int_or_zero(cid)),
                    "Page": str(page),
                    "shareKey": share_code,
                    "SharePwd": receive_code,
                },
            )
            payload_data = self._response_data(data)
            if not share_title:
                share_title = str(
                    payload_data.get("ShareName")
                    or payload_data.get("shareName")
                    or payload_data.get("title")
                    or ""
                ).strip()
            if not total:
                total = self._int_or_zero(payload_data.get("total", payload_data.get("Total", 0)))
            items = self._extract_item_list(data)
            if not items:
                break
            for item in items:
                if total_seen < normalized_offset:
                    total_seen += 1
                    continue
                entry = self._normalize_entry(item, cid)
                entry["share_id"] = share_code
                entries.append(entry)
                total_seen += 1
                if len(entries) >= normalized_limit:
                    break
            next_marker = self._extract_next_marker(data)
            if next_marker == "-1":
                break
            page += 1
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": total or total_seen or len(entries),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
            "share": dict(share_payload),
            "share_title": share_title or str(share_payload.get("title", "") or share_payload.get("share_name", "") or "").strip(),
        }

    def prepare_share_receive(self, cookie, share_payload, cid="0"):
        return {**share_payload, "target_cid": cid or "0"}

    def _share_entry_has_copy_metadata(self, item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        if not str(self._item_value(item, "id", "fileId", "fileID", "FileId", "FileID", default="") or "").strip():
            return False
        if not str(self._item_value(item, "name", "fileName", "FileName", default="") or "").strip():
            return False
        return (
            self._item_value(item, "etag", "Etag", "ETag", default=None) is not None
            and self._item_value(item, "size", "Size", "fileSize", "FileSize", default=None) is not None
        )

    def _hydrate_share_receive_entries(self, cookie, receive_payload: dict, files: list) -> list:
        selected_entries = [item for item in (files or []) if isinstance(item, dict)]
        if selected_entries and all(self._share_entry_has_copy_metadata(item) for item in selected_entries):
            return selected_entries

        selected_ids = {
            str(self._item_value(item, "id", "fileId", "fileID", "FileId", "FileID", default="") or "").strip()
            for item in selected_entries
        }
        selected_ids.update(
            str(item or "").strip()
            for item in (receive_payload.get("selected_ids", []) if isinstance(receive_payload, dict) else [])
        )
        selected_ids = {item for item in selected_ids if item}
        if not selected_ids:
            return selected_entries

        parent_ids = {
            str(self._item_value(item, "parent_id", "parentFileId", "ParentFileId", default="0") or "0").strip() or "0"
            for item in selected_entries
        }
        parent_ids.add("0")

        hydrated = {}
        share_payload = {
            "share_code": receive_payload.get("share_code", ""),
            "receive_code": receive_payload.get("receive_code", ""),
            "url": receive_payload.get("url", ""),
        }
        for parent_id in list(parent_ids):
            try:
                snapshot = self.list_share_entries(cookie, share_payload, parent_id or "0", 0, 400)
            except Exception:
                continue
            for entry in snapshot.get("entries", []) if isinstance(snapshot, dict) else []:
                entry_id = str(entry.get("id", "") or "").strip()
                if entry_id in selected_ids:
                    hydrated[entry_id] = entry
            if selected_ids.issubset(hydrated.keys()):
                break

        merged_entries = []
        for item in selected_entries:
            entry_id = str(self._item_value(item, "id", "fileId", "fileID", "FileId", "FileID", default="") or "").strip()
            hydrated_entry = hydrated.get(entry_id, {})
            if not hydrated_entry:
                merged_entries.append(item)
                continue
            merged = dict(item)
            for source_key, target_key in (
                ("size", "size"),
                ("etag", "etag"),
                ("s3key_flag", "s3key_flag"),
                ("drive_id", "drive_id"),
            ):
                if merged.get(target_key) in (None, "", 0):
                    merged[target_key] = hydrated_entry.get(source_key, merged.get(target_key))
            merged_entries.append(merged)
        existing_ids = {
            str(self._item_value(item, "id", "fileId", "fileID", "FileId", "FileID", default="") or "").strip()
            for item in merged_entries
        }
        for entry_id in selected_ids - existing_ids:
            if entry_id in hydrated:
                merged_entries.append(hydrated[entry_id])
        return merged_entries

    def _copy_save_current_level(self, target_cid) -> int:
        return 1 if self._int_or_zero(target_cid) > 0 else 0

    def _is_invalid_transfer_id_message(self, value) -> bool:
        message = str(value or "").strip()
        return "转存ID无效" in message

    def submit_share_receive(self, cookie, receive_payload, files):
        share_code = receive_payload["share_code"]
        receive_code = receive_payload.get("receive_code", "")
        target_cid = receive_payload.get("target_cid", "0")
        target_parent_id = self._int_or_zero(target_cid)
        file_list = []
        selected_entries = self._hydrate_share_receive_entries(cookie, receive_payload, files)
        for item in selected_entries:
            if not isinstance(item, dict):
                continue
            file_id = str(self._item_value(item, "id", "fileId", "fileID", "FileId", "FileID", default="") or "").strip()
            if not file_id:
                continue
            is_dir = bool(item.get("is_dir")) or str(item.get("type", "")).strip().lower() == "folder"
            try:
                size = int(self._item_value(item, "size", "Size", "fileSize", "FileSize", default=0) or 0)
            except (TypeError, ValueError):
                size = 0
            file_name = str(self._item_value(item, "name", "fileName", "FileName", default="") or "").strip()
            file_name = file_name.rsplit("/", 1)[-1].strip() or file_name
            file_list.append({
                "fileID": self._int_or_zero(file_id),
                "size": size,
                "etag": str(self._item_value(item, "etag", "Etag", "ETag", default="") or ""),
                "type": 1 if is_dir else 0,
                "parentFileID": target_parent_id,
                "fileName": file_name,
                "driveID": self._int_or_zero(self._item_value(item, "drive_id", "driveId", "DriveId", "driveID", "DriveID", default=self.drive_id)),
            })
        file_ids = [item["fileID"] for item in file_list if item.get("fileID")]
        if not file_ids:
            raise RuntimeError("未选择要转存的文件")

        current_level = self._copy_save_current_level(target_parent_id)
        request_body = {
            "fileList": file_list,
            "shareKey": share_code,
            "sharePwd": receive_code or None,
            "currentLevel": current_level,
            "superAdmin": None,
        }
        try:
            data = self._api_call(
                cookie, "POST",
                "https://www.123pan.com/b/api/restful/goapi/v1/file/copy/save",
                json=request_body,
                timeout=60,
            )
        except RuntimeError as exc:
            if not self._is_invalid_transfer_id_message(exc):
                raise
            retry_body = dict(request_body)
            retry_body["currentLevel"] = 0 if current_level else 1
            data = self._api_call(
                cookie, "POST",
                "https://www.123pan.com/b/api/restful/goapi/v1/file/copy/save",
                json=retry_body,
                timeout=60,
            )
        return {"success": True, "count": len(file_ids), "response": data}

    def submit_offline_task(self, cookie, resource_url, folder_id="0"):
        raise RuntimeError("123云盘当前账号密码链路不支持 magnet 离线下载，请改用 115 网盘下载磁力资源")

    def probe_connectivity(self, cookie):
        self._api_call(cookie, "GET", "https://www.123pan.com/b/api/user/info")
        return True

    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        numeric_id = self._require_positive_int_id(entry_id, "重命名")
        self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/rename",
            json={"driveId": self.drive_id, "DriveId": self.drive_id, "fileId": numeric_id, "fileName": new_name.strip()},
        )
        return {"ok": True, "id": entry_id, "name": new_name}

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        numeric_ids = self._normalize_entry_ids(entry_ids, "移动")
        self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/mod_pid",
            json={
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "fileIdList": [{"FileId": item_id} for item_id in numeric_ids],
                "parentFileId": self._int_or_zero(target_id),
            },
        )
        return {"ok": True, "ids": entry_ids, "target_cid": target_id}

    def copy_entries(self, cookie, entry_ids, target_id, source_id=""):
        raise RuntimeError("123云盘暂不支持复制")

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        numeric_ids = self._normalize_entry_ids(entry_ids, "删除")
        parent_cid = str(parent_id or "0").strip() or "0"
        parent_payload = self._list_entries_api_payload(cookie, parent_cid)
        current_items = self._extract_item_list(parent_payload)
        item_by_id = {}
        for item in current_items:
            item_id = self._int_or_zero(self._item_value(item, "fileId", "fileID", "FileId", "FileID", "FileIdStr", "id", "Id", default=0))
            if item_id > 0:
                item_by_id[item_id] = item
        missing_ids = [item_id for item_id in numeric_ids if item_id not in item_by_id]
        if missing_ids:
            raise RuntimeError(f"123云盘删除失败：未在当前目录找到 {len(missing_ids)} 个待删除条目，请刷新目录后重试")
        trash_info_list = [self._build_trash_file_info(item_by_id[item_id]) for item_id in numeric_ids]
        self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/trash",
            json={
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "fileTrashInfoList": trash_info_list,
                "operation": True,
                "Operation": True,
            },
        )
        return {"ok": True, "ids": entry_ids}

register(Pan123Provider())
