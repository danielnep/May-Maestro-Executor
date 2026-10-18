"""Uncredentialed public research. Retrieved text is evidence, never instructions.

No proxy/environment credentials, cookies, Javascript, or host command execution.
Search endpoints are best-effort public HTML/RSS, not a guaranteed paid API.
"""
import http.client
import ipaddress
import re
import socket
import ssl
import time
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

MAX_BYTES = 512_000
USER_AGENT = 'HorizonResearch/1.0 (public-document research; no JavaScript)'


class ResearchError(RuntimeError):
    pass


def _url(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\s\x00-\x1f\x7f\\]', url):
        raise ResearchError('Invalid public URL')
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError):
        raise ResearchError('Only credential-free public HTTPS URLs on port 443 are allowed') from None
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '%' in host:
        raise ResearchError('Private hostname forbidden')
    return parsed, host


def _public_ip(value):
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_reserved


def _addresses(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ResearchError('Public hostname resolution failed') from None
    if not addresses or any(not _public_ip(item[4][0]) for item in addresses):
        raise ResearchError('Nonpublic destination forbidden')
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, addresses, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.addresses = addresses

    def connect(self):
        # Connect directly to the already validated address, never resolve twice.
        last = None
        for family, kind, proto, _, sockaddr in self.addresses:
            raw = socket.socket(family, kind, proto)
            raw.settimeout(self.timeout)
            try:
                raw.connect(sockaddr)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as error:
                last = error
                raw.close()
        raise last or OSError('No public address')


def _download(url):
    deadline = time.monotonic() + 25
    for _ in range(5):
        parsed, host = _url(url)
        addresses = _addresses(host)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResearchError('Public request timed out')
        connection = _PinnedHTTPS(host, addresses, min(10, remaining))
        try:
            target = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            connection.request('GET', target, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain,application/xml,text/xml,application/json', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ResearchError('Redirect missing destination')
                url = urllib.parse.urljoin(url, location)
                continue  # New URL and every DNS answer are validated next iteration.
            if response.status != 200:
                raise ResearchError('Public endpoint returned HTTP ' + str(response.status))
            kind = (response.getheader('Content-Type') or '').split(';')[0].lower()
            if kind not in ('text/html', 'text/plain', 'application/xml', 'text/xml', 'application/rss+xml', 'application/json', 'application/xhtml+xml'):
                raise ResearchError('Public response is not supported text')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ResearchError('Compressed responses are not accepted')
            chunks, size = [], 0
            while size <= MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ResearchError('Public request timed out')
                if connection.sock:
                    connection.sock.settimeout(min(5, remaining))
                chunk = response.read(min(16_384, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            body = b''.join(chunks)
            return url, kind, body[:MAX_BYTES].decode('utf-8', errors='replace'), size > MAX_BYTES
        except (OSError, http.client.HTTPException):
            raise ResearchError('Public request failed') from None
        finally:
            connection.close()
    raise ResearchError('Too many public redirects')


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.skip = [], [], 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            if self.in_title:
                self.title.append(data)


def fetch_public(url):
    """Fetch only public HTTPS text. All returned contents remain untrusted."""
    final, kind, content, truncated = _download(url)
    title = ''
    if kind in ('text/html', 'application/xhtml+xml'):
        parser = _Text()
        parser.feed(content)
        content = ' '.join(' '.join(parser.parts).split())
        title = ' '.join(parser.title).strip()
    return {'url': final, 'title': title[:500], 'text': content[:60_000], 'content_type': kind,
            'truncated': truncated or len(content) > 60_000, 'untrusted': True}


def _result_url(url):
    if url.startswith('//'):
        url = 'https:' + url
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname and parsed.hostname.endswith('duckduckgo.com') and parsed.path == '/l/':
        url = urllib.parse.parse_qs(parsed.query).get('uddg', [''])[0]
    try:
        _, host = _url(url)
        # Search snippets are not fetched here; literal private destinations rejected.
        try:
            if not _public_ip(host):
                return None
        except ValueError:
            pass
        return url
    except ResearchError:
        return None


class _DDG(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self.active, self.words = [], None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'result__a' in attrs.get('class', '').split():
            self.active = _result_url(attrs.get('href', ''))
            self.words = []

    def handle_data(self, data):
        if self.active:
            self.words.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active:
            self.results.append({'url': self.active, 'title': ' '.join(' '.join(self.words).split()), 'snippet': ''})
            self.active = None


def search_web(query):
    """Search the explicit query only; no conversation, files or keys are sent."""
    if not isinstance(query, str) or not query.strip() or len(query) > 1000 or re.search(r'[\x00-\x1f\x7f]', query):
        raise ResearchError('Search requires a nonempty query of at most 1000 characters')
    query = query.strip()
    encoded = urllib.parse.urlencode({'q': query})
    providers = [('duckduckgo-html', 'https://html.duckduckgo.com/html/?' + encoded),
                 ('bing-rss', 'https://www.bing.com/search?format=rss&' + encoded)]
    for provider, endpoint in providers:
        try:
            _, _, body, truncated = _download(endpoint)
            if truncated:
                raise ResearchError('Search response exceeds technical size limit')
            if provider == 'duckduckgo-html':
                parser = _DDG()
                parser.feed(body)
                results = parser.results
            else:
                if '<!DOCTYPE' in body.upper() or '<!ENTITY' in body.upper():
                    raise ResearchError('XML document declarations forbidden')
                root = ET.fromstring(body)
                results = []
                for item in root.findall('./channel/item'):
                    link = _result_url(item.findtext('link', ''))
                    if link:
                        results.append({'url': link, 'title': item.findtext('title', '')[:500],
                                        'snippet': item.findtext('description', '')[:1500]})
            distinct = {result['url']: result for result in results}
            if distinct:
                return {'query': query, 'provider': provider, 'results': list(distinct.values())[:10], 'untrusted': True}
        except (ResearchError, ET.ParseError, ValueError):
            continue
    raise ResearchError('Public search unavailable or returned no verifiable links; try a different query or fetch a known official URL')
