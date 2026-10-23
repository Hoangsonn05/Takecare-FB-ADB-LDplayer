from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Callable
from urllib.parse import (
    SplitResult,
    parse_qsl,
    urlencode,
    unquote,
    urljoin,
    urlsplit,
    urlunsplit,
)

import requests

from core.config import FACEBOOK_PACKAGE_NAME
from integrations.golike_api import NormalizedJob

from .models import ContentKind

_FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "web.facebook.com",
    "fb.watch",
    "fb.me",
}
_SHORT_HOSTS = {"fb.me", "fb.watch"}
_TRACKING_QUERY_KEYS = {
    "__cft__",
    "__tn__",
    "acontext",
    "extid",
    "fbclid",
    "mibextid",
    "paipv",
    "ref",
    "refsrc",
    "sfnsn",
}
_TARGET_QUERY_KEYS = ("story_fbid", "fbid", "v")
_LOGIN_PATH_PREFIXES = ("/login", "/checkpoint", "/recover")


class InvalidFacebookUrl(ValueError):
    pass


class InvalidFacebookRedirect(InvalidFacebookUrl):
    pass


def _is_facebook_host(host: str) -> bool:
    value = str(host or "").casefold().rstrip(".")
    return value in _FACEBOOK_HOSTS or value.endswith(".facebook.com")


def _strip_tracking_query(query: str) -> str:
    clean: list[tuple[str, str]] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        clean.append((key, value))
    return urlencode(clean, doseq=True)


def normalize_facebook_url(url: str) -> str:
    text = html.unescape(str(url or "")).strip()
    parsed = urlsplit(text)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise InvalidFacebookUrl("Job link must use http or https")
    if parsed.username or parsed.password:
        raise InvalidFacebookUrl("Job link must not contain URL credentials")
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        raise InvalidFacebookUrl("Job link is missing a host")
    if not _is_facebook_host(host):
        raise InvalidFacebookUrl(f"Unsupported Facebook host: {host}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidFacebookUrl("Job link contains an invalid port") from exc
    if port not in (None, 80, 443):
        raise InvalidFacebookUrl("Job link uses an unsupported port")
    if host not in _SHORT_HOSTS:
        host = "www.facebook.com"
    normalized = SplitResult(
        parsed.scheme.casefold(),
        host,
        re.sub(r"/{2,}", "/", parsed.path or "/"),
        _strip_tracking_query(parsed.query),
        "",
    )
    return urlunsplit(normalized)


def _content_kind(url: str) -> ContentKind:
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "/reel/" in path or "/reels/" in path or "/share/r/" in path:
        return ContentKind.REEL
    if (
        any(
            marker in path
            for marker in (
                "/posts/",
                "/story.php",
                "/permalink.php",
                "/photo",
                "/videos/",
                "/watch/",
                "/share/p/",
                "/share/v/",
            )
        )
        or any(key in query for key in _TARGET_QUERY_KEYS)
    ):
        return ContentKind.FEED
    return ContentKind.UNKNOWN


def _target_ids(url: str) -> set[str]:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    values = {
        str(query[key]).strip()
        for key in _TARGET_QUERY_KEYS
        if str(query.get(key) or "").strip()
    }
    if not values and str(query.get("id") or "").strip():
        values.add(str(query["id"]).strip())
    path_patterns = (
        r"/(?:reel|reels|posts)/([^/?#]+)",
        r"/videos/(?:[^/?#]+/)?([^/?#]+)",
    )
    for pattern in path_patterns:
        match = re.search(pattern, parsed.path, flags=re.IGNORECASE)
        if match:
            values.add(unquote(match.group(1)).strip())
    return {value for value in values if value}


def _is_home_or_login(url: str) -> bool:
    parsed = urlsplit(url)
    path = (parsed.path or "/").casefold()
    if path in {"", "/"}:
        return True
    return any(path.startswith(prefix) for prefix in _LOGIN_PATH_PREFIXES)


def _validate_candidate_identity(url: str, object_id: str | None) -> None:
    if _is_home_or_login(url):
        raise InvalidFacebookUrl("Facebook target resolves to Home or login")
    expected = str(object_id or "").strip()
    if not expected:
        return
    ids = _target_ids(url)
    if expected in ids or not ids:
        return
    if expected.isdigit() and any(value.isdigit() for value in ids):
        raise InvalidFacebookUrl("Job object_id conflicts with the target URL")


@dataclass(slots=True, frozen=True)
class FacebookJobTarget:
    original_url: str
    canonical_url: str
    candidate_urls: tuple[str, ...]
    expected_object_id: str | None
    expected_content_kind: ContentKind
    source: str


class FacebookJobTargetResolver:
    def __init__(
        self,
        *,
        short_link_resolve_timeout_sec: float = 8.0,
        max_redirects: int = 5,
        resolve_short_links: bool = True,
        request_fn: Callable[..., object] | None = None,
    ) -> None:
        self.timeout_sec = float(short_link_resolve_timeout_sec)
        self.max_redirects = int(max_redirects)
        self.resolve_short_links = bool(resolve_short_links)
        self._request = request_fn or requests.request
        self._redirect_cache: dict[str, tuple[bool, str]] = {}

    @staticmethod
    def _is_short_or_share(url: str) -> bool:
        parsed = urlsplit(url)
        path = parsed.path.casefold()
        return bool(
            (parsed.hostname or "").casefold() in _SHORT_HOSTS
            or path.startswith(("/share/p/", "/share/r/", "/share/v/"))
        )

    @staticmethod
    def _unwrap_tracking_redirect(url: str) -> str | None:
        parsed = urlsplit(url)
        if not (parsed.hostname or "").casefold().endswith("facebook.com"):
            return None
        if parsed.path.casefold() not in {"/l.php", "/flx/warn/"}:
            return None
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        target = str(query.get("u") or query.get("url") or "").strip()
        return normalize_facebook_url(unquote(target)) if target else None

    def _request_without_credentials(self, method: str, url: str):
        return self._request(
            method,
            url,
            allow_redirects=False,
            timeout=self.timeout_sec,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0 FacebookRouteProbe/1.0"},
        )

    def _resolve_redirect(self, original_url: str) -> str | None:
        cached = self._redirect_cache.get(original_url)
        if cached is not None:
            ok, value = cached
            if ok:
                return value or None
            raise InvalidFacebookRedirect(value)

        current = original_url
        try:
            for hop in range(self.max_redirects + 1):
                response = self._request_without_credentials("HEAD", current)
                status = int(getattr(response, "status_code", 0) or 0)
                if status in {405, 501}:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                    response = self._request_without_credentials("GET", current)
                    status = int(getattr(response, "status_code", 0) or 0)
                headers = getattr(response, "headers", {}) or {}
                location = str(headers.get("Location") or headers.get("location") or "").strip()
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                if 300 <= status < 400:
                    if not location:
                        raise InvalidFacebookRedirect("Facebook redirect is missing Location")
                    if hop >= self.max_redirects:
                        raise InvalidFacebookRedirect("Facebook redirect limit exceeded")
                    next_url = urljoin(current, location)
                    try:
                        current = normalize_facebook_url(next_url)
                    except InvalidFacebookUrl as exc:
                        raise InvalidFacebookRedirect(
                            "Facebook short link redirects outside Facebook"
                        ) from exc
                    continue
                if status >= 400 or status == 0:
                    self._redirect_cache[original_url] = (True, "")
                    return None
                final_url = normalize_facebook_url(
                    str(getattr(response, "url", "") or current)
                )
                if _is_home_or_login(final_url):
                    raise InvalidFacebookRedirect(
                        "Facebook short link resolves to Home or login"
                    )
                self._redirect_cache[original_url] = (True, final_url)
                return final_url
        except InvalidFacebookRedirect as exc:
            self._redirect_cache[original_url] = (False, str(exc))
            raise
        except requests.RequestException:
            self._redirect_cache[original_url] = (True, "")
            return None
        return None

    def resolve(self, job: NormalizedJob) -> FacebookJobTarget:
        original_text = html.unescape(str(job.link or "")).strip()
        normalized_original = normalize_facebook_url(original_text)
        _validate_candidate_identity(normalized_original, job.object_id)

        source = "direct"
        canonical = normalized_original
        unwrapped = self._unwrap_tracking_redirect(normalized_original)
        if unwrapped:
            source = "facebook_redirect"
            canonical = unwrapped
        elif self._is_short_or_share(normalized_original):
            source = "short_or_share"
            if self.resolve_short_links:
                resolved = self._resolve_redirect(normalized_original)
                if resolved:
                    canonical = resolved

        candidates: list[str] = []
        invalid_identity: InvalidFacebookUrl | None = None
        for candidate in (canonical, normalized_original):
            if candidate in candidates:
                continue
            try:
                _validate_candidate_identity(candidate, job.object_id)
            except InvalidFacebookUrl as exc:
                invalid_identity = exc
                continue
            candidates.append(candidate)
            if len(candidates) == 2:
                break
        if not candidates:
            raise invalid_identity or InvalidFacebookUrl("Job target is invalid")

        canonical = candidates[0]
        kind = _content_kind(canonical)
        if kind is ContentKind.UNKNOWN:
            for candidate in candidates[1:]:
                kind = _content_kind(candidate)
                if kind is not ContentKind.UNKNOWN:
                    break
        return FacebookJobTarget(
            original_url=original_text,
            canonical_url=canonical,
            candidate_urls=tuple(candidates),
            expected_object_id=str(job.object_id) if job.object_id else None,
            expected_content_kind=kind,
            source=source,
        )


@dataclass(slots=True)
class FacebookRouteOpenResult:
    command_accepted: bool
    candidate_url: str
    resolved_component: str
    status: str
    launched_activity: str
    this_time_ms: int | None
    total_time_ms: int | None
    wait_time_ms: int | None
    stdout: str
    stderr: str
    security_rejected: bool
    url_family: str = ""
    facebook_version_code: str = ""
    queried_activity_count: int = 0
    returncode: int = 0
    timed_out: bool = False

    @property
    def status_ok(self) -> bool:
        return self.command_accepted

    @property
    def normalized_url(self) -> str:
        return self.candidate_url


class FacebookJobRouter:
    def __init__(
        self,
        package_name: str = FACEBOOK_PACKAGE_NAME,
        *,
        target_resolver: FacebookJobTargetResolver | None = None,
    ) -> None:
        self.package_name = package_name
        self.target_resolver = target_resolver or FacebookJobTargetResolver()
        self._component_cache: dict[tuple[str, str, str], str | None] = {}

    def prepare_target(self, job: NormalizedJob) -> FacebookJobTarget:
        return self.target_resolver.resolve(job)

    @staticmethod
    def url_family(url: str, kind: ContentKind = ContentKind.UNKNOWN) -> str:
        parsed = urlsplit(url)
        path = parsed.path.casefold()
        if (parsed.hostname or "").casefold() in _SHORT_HOSTS or "/share/" in path:
            return "share"
        if kind is ContentKind.REEL or "/reel" in path:
            return "reel"
        if "/video" in path or "/watch" in path or "v=" in parsed.query:
            return "video"
        return "feed"

    @staticmethod
    def _looks_like_launcher(component: str) -> bool:
        class_name = component.partition("/")[2].casefold().rsplit(".", 1)[-1]
        return class_name in {"main", "mainactivity", "launcher", "launcheractivity"}

    def _resolved_component(
        self,
        device,
        candidate_url: str,
        kind: ContentKind,
    ) -> tuple[str, str, str, int]:
        version_code = str(device.package_version_code(self.package_name) or "unknown")
        family = self.url_family(candidate_url, kind)
        key = (str(device.serial), version_code, family)
        if key in self._component_cache:
            return self._component_cache[key] or "", version_code, family, 0

        components = list(
            dict.fromkeys(
                device.query_view_activities(candidate_url, self.package_name, user_id=0)
            )
        )
        launcher_components: set[str] = set()
        query_launchers = getattr(device, "query_launcher_activities", None)
        if callable(query_launchers):
            launcher_components = set(query_launchers(self.package_name, user_id=0))
        selected = next(
            (
                component
                for component in components
                if component.startswith(f"{self.package_name}/")
                and component not in launcher_components
                and not self._looks_like_launcher(component)
            ),
            "",
        )
        self._component_cache[key] = selected or None
        return selected, version_code, family, len(components)

    @staticmethod
    def _field(output: str, name: str) -> str:
        match = re.search(
            rf"(?im)^\s*{re.escape(name)}\s*[:=]\s*(.*?)\s*$",
            output,
        )
        return match.group(1).strip() if match else ""

    @classmethod
    def _integer_field(cls, output: str, name: str) -> int | None:
        match = re.search(r"\d+", cls._field(output, name))
        return int(match.group(0)) if match else None

    def open_candidate(
        self,
        device,
        target: FacebookJobTarget,
        candidate_url: str,
    ) -> FacebookRouteOpenResult:
        component, version_code, family, queried_count = self._resolved_component(
            device,
            candidate_url,
            target.expected_content_kind,
        )
        if not component:
            return FacebookRouteOpenResult(
                False,
                candidate_url,
                "",
                "FACEBOOK_VIEW_HANDLER_NOT_FOUND",
                "",
                None,
                None,
                None,
                "",
                "",
                False,
                family,
                version_code,
                queried_count,
            )

        result = device.launch_explicit_view_activity(
            component=component,
            url=candidate_url,
            user_id=0,
        )
        stdout = str(result.stdout or "")
        stderr = str(result.stderr or "")
        output = f"{stdout}\n{stderr}"
        raw_status = self._field(output, "Status")
        security_rejected = any(
            marker in output.casefold()
            for marker in ("securityexception", "permission denial", "not exported")
        )
        status_ok = raw_status.casefold() == "ok"
        accepted = bool(result.ok and status_ok and not security_rejected)
        status = (
            "FACEBOOK_ROUTE_COMPONENT_REJECTED"
            if security_rejected
            else raw_status
            if accepted
            else "FACEBOOK_ROUTE_COMMAND_FAILED"
        )
        return FacebookRouteOpenResult(
            accepted,
            candidate_url,
            component,
            status,
            self._field(output, "Activity"),
            self._integer_field(output, "ThisTime"),
            self._integer_field(output, "TotalTime"),
            self._integer_field(output, "WaitTime"),
            stdout,
            stderr,
            security_rejected,
            family,
            version_code,
            queried_count,
            int(getattr(result, "returncode", 1)),
            bool(getattr(result, "timed_out", False)),
        )
