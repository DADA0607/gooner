from flask import Flask, jsonify, request, render_template_string, Response, stream_with_context
import requests
import re
import json
import os
from datetime import datetime, timedelta
import time
import logging
import zlib
import gzip
from io import BytesIO
import urllib.parse
from urllib.parse import urljoin, urlparse, quote, unquote

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://faphouse2.com"
EMAIL = os.environ.get('EMAIL', 'email') #add email
PASSWORD = os.environ.get('PASSWORD', 'pass') #add pass
CACHE_DURATION = 300

class FaphouseClient:
    def __init__(self):
        self.session = None
        self.logged_in = False
        self.session_created = False
        self._m3u8_cache = {}
        self._m3u8_cache_max = 100

    def _cache_get(self, key):
        if key in self._m3u8_cache:
            entry = self._m3u8_cache[key]
            if (datetime.now() - entry['ts']).seconds < CACHE_DURATION:
                return entry['url']
            del self._m3u8_cache[key]
        return None

    def _cache_set(self, key, url):
        if len(self._m3u8_cache) >= self._m3u8_cache_max:
            oldest = min(self._m3u8_cache, key=lambda k: self._m3u8_cache[k]['ts'])
            del self._m3u8_cache[oldest]
        self._m3u8_cache[key] = {'url': url, 'ts': datetime.now()}
        
    def ensure_session(self):
        if not self.session or not self.logged_in:
            logger.info("Creating new session...")
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            self.login()
        return self.session
    
    def login(self):
        logger.info(f"Attempting login with email: {EMAIL[:5]}...")
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/json',
            'Origin': BASE_URL,
            'Referer': f'{BASE_URL}/',
            'DNT': '1',
            'Connection': 'keep-alive'
        })
        
        try:
            logger.info("Getting initial page...")
            init_res = self.session.get(BASE_URL, timeout=10)
            logger.info(f"Initial page status: {init_res.status_code}")
            
            payload = {
                "login": EMAIL,
                "password": PASSWORD,
                "rememberMe": "1",
                "recaptcha": "",
                "trackingParamsBag": "eyJwcm9tb19pZCI6IiIsInZpZGVvX2lkIjpudWxsLCJzdHVkaW9faWQiOm51bGwsInByb2R1Y2VyX2lkIjpudWxsLCJvcmllbnRhdGlvbiI6InN0cmFpZ2h0IiwibWxfcGFnZSI6Im1haW5fcGFnZSIsIm1sX3BhZ2VfdmFsdWVfaWQiOm51bGwsIm1sX3BhZ2VfdmFsdWUiOm51bGwsIm1sX3BhZ2VfbnVtYmVyIjpudWxsLCJtbF9yZWZfcGFnZV92YWx1ZV9pZCI6bnVsbCwibWxfcmVmX3BhZ2VfdmFsdWUiOiIiLCJtbF9yZWZfcGFnZV9udW1iZXIiOm51bGwsIm1sX3JlZl9wYWdlIjoiZGlyZWN0In0="
            }
            
            logger.info("Sending login request...")
            login_res = self.session.post(
                f"{BASE_URL}/api/auth/signin",
                json=payload,
                timeout=15
            )
            
            logger.info(f"Login response status: {login_res.status_code}")
            
            if login_res.status_code == 200:
                try:
                    data = login_res.json()
                    if data.get('success') or data.get('data'):
                        self.logged_in = True
                        logger.info("Login successful!")
                        return True
                except:
                    pass
                
                if len(self.session.cookies) > 0:
                    self.logged_in = True
                    logger.info("Login successful (session established)!")
                    return True
            
            self.logged_in = False
            return False
            
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            self.logged_in = False
            return False
    
    def _decode_response(self, response):
        try:
            content_encoding = response.headers.get('Content-Encoding', '')
            
            if content_encoding:
                logger.info(f"Decoding {content_encoding} response...")
            
            if 'gzip' in content_encoding:
                try:
                    return gzip.decompress(response.content).decode('utf-8', errors='ignore')
                except:
                    pass
            
            if 'deflate' in content_encoding:
                try:
                    return zlib.decompress(response.content).decode('utf-8', errors='ignore')
                except:
                    try:
                        return zlib.decompress(response.content, -zlib.MAX_WBITS).decode('utf-8', errors='ignore')
                    except:
                        pass
            
            if 'br' in content_encoding:
                try:
                    import brotli
                    return brotli.decompress(response.content).decode('utf-8', errors='ignore')
                except ImportError:
                    logger.warning("Brotli not installed, skipping...")
                except:
                    pass
            
            try:
                return response.text
            except:
                pass
            
            return response.text if response.text else str(response.content)
            
        except Exception as e:
            logger.error(f"Decoding error: {str(e)}")
            return response.text if response.text else str(response.content)
    
    def get_m3u8_url(self, video_url):
        logger.info(f"Processing video URL: {video_url[:80]}...")
        
        if '#' in video_url:
            video_url = video_url.split('#')[0]
        
        cached = self._cache_get(video_url)
        if cached:
            logger.info("Returning cached M3U8 URL")
            return cached
        
        session = self.ensure_session()
        if session:
            try:
                logger.info("Attempt 1: Using authenticated session...")
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Referer': BASE_URL,
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                }
                
                response = session.get(video_url, timeout=15, headers=headers)
                logger.info(f"Session GET Status: {response.status_code}")
                
                if response.status_code == 200:
                    html = self._decode_response(response)
                    if html:
                        m3u8 = self._extract_m3u8(html)
                        if m3u8:
                            logger.info("Found M3U8 URL with session!")
                            self._cache_set(video_url, m3u8)
                            return m3u8
            except Exception as e:
                logger.warning(f"Session attempt failed: {str(e)}")
        
        logger.info("Attempt 2: Trying guest fetch...")
        try:
            guest_session = requests.Session()
            guest_session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': BASE_URL,
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            
            response = guest_session.get(video_url, timeout=15)
            logger.info(f"Guest Status: {response.status_code}")
            
            if response.status_code == 200:
                html = self._decode_response(response)
                if html:
                    m3u8 = self._extract_m3u8(html)
                    if m3u8:
                        logger.info("Found M3U8 URL with guest!")
                        self._cache_set(video_url, m3u8)
                        return m3u8
        except Exception as e:
            logger.warning(f"Guest attempt failed: {str(e)}")
        
        logger.error("Failed to find M3U8 URL with all attempts.")
        return None
    
    def _extract_m3u8(self, html_content):
        if not html_content:
            return None
        
        html_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', html_content)
        
        patterns = [
            r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
            r'https?://[^\s"\'<>]+\.m3u8(?:\?[^\s"\'<>]*)?',
            r'//[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
            r'src\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'src\s*=\s*["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
            r'href\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'file\s*:\s*["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
            r'url\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'url\s*:\s*["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
            r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        ]
        
        found_urls = []
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if matches:
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    m3u8_url = match.strip()
                    if '"' in m3u8_url:
                        m3u8_url = m3u8_url.split('"')[0]
                    if "'" in m3u8_url:
                        m3u8_url = m3u8_url.split("'")[0]
                    if '&amp;' in m3u8_url:
                        m3u8_url = m3u8_url.replace('&amp;', '&')
                    
                    if m3u8_url.startswith('//'):
                        m3u8_url = 'https:' + m3u8_url
                    
                    if m3u8_url.startswith('http') and '.m3u8' in m3u8_url:
                        found_urls.append(m3u8_url)
        
        seen = set()
        unique_urls = []
        for url in found_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        if unique_urls:
            logger.info(f"Found {len(unique_urls)} M3U8 URLs")
            return unique_urls[0]
        
        return None

    def fetch_m3u8_proxied(self, m3u8_url):
        self.ensure_session()
        try:
            resp = self.session.get(m3u8_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br',
            })
            if resp.status_code != 200:
                return None
            content_encoding = resp.headers.get('Content-Encoding', '')
            body = None
            if 'gzip' in content_encoding:
                try:
                    body = gzip.decompress(resp.content).decode('utf-8', errors='ignore')
                except Exception:
                    body = None
            if body is None and 'br' in content_encoding:
                try:
                    import brotli
                    body = brotli.decompress(resp.content).decode('utf-8', errors='ignore')
                except ImportError:
                    body = None
                except Exception:
                    body = None
            if body is None and 'deflate' in content_encoding:
                try:
                    body = zlib.decompress(resp.content).decode('utf-8', errors='ignore')
                except Exception:
                    try:
                        body = zlib.decompress(resp.content, -zlib.MAX_WBITS).decode('utf-8', errors='ignore')
                    except Exception:
                        body = None
            if body is None:
                body = resp.text
            return self._rewrite_m3u8(body, m3u8_url)
        except Exception as e:
            logger.error(f"Proxy fetch error: {e}")
            return None

    def _rewrite_m3u8(self, content, base_url):
        lines = content.split('\n')
        parsed_base = urlparse(base_url)
        base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        result = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                if stripped.startswith('#EXT-X-MAP') and 'URI=' in stripped:
                    uri_match = re.search(r'URI="([^"]+)"', stripped)
                    if uri_match:
                        orig_uri = uri_match.group(1)
                        abs_uri = self._resolve_url(orig_uri, base_url, base_origin)
                        if abs_uri.endswith('.m3u8'):
                            proxy_uri = f"/proxy/m3u8?url={quote(abs_uri, safe='')}"
                        else:
                            proxy_uri = f"/proxy/media?url={quote(abs_uri, safe='')}"
                        stripped = stripped[:uri_match.start(1)] + proxy_uri + stripped[uri_match.end(1):]
                if stripped.startswith('#EXT-X-STREAM-INF'):
                    next_url = ''
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip() and not lines[j].strip().startswith('#'):
                            next_url = lines[j].strip()
                            break
                    is_av1 = (('.av1.' in next_url or 'av01' in next_url) or
                              ('.av1.' in base_url or 'av01' in base_url))
                    if is_av1:
                        stripped = re.sub(r'avc1[^\s,"]+', 'av01.0.08M.08', stripped)
                result.append(stripped)
            else:
                abs_url = self._resolve_url(stripped, base_url, base_origin)
                if abs_url.endswith('.m3u8'):
                    result.append(f"/proxy/m3u8?url={quote(abs_url, safe='')}")
                else:
                    result.append(f"/proxy/media?url={quote(abs_url, safe='')}")
        return '\n'.join(result)

    def _resolve_url(self, url, base_url, base_origin):
        if url.startswith('http://') or url.startswith('https://'):
            return url
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return base_origin + url
        return urljoin(base_url, url)

    def fetch_media_proxied(self, media_url, range_header=None):
        self.ensure_session()
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Encoding': 'identity',
                'Referer': 'https://faphouse2.com/',
            }
            if range_header:
                headers['Range'] = range_header
            resp = self.session.get(media_url, headers=headers, timeout=30, stream=True)
            if resp.status_code not in (200, 206):
                return Response(status=resp.status_code)
            resp_headers = {
                'Content-Type': resp.headers.get('Content-Type', 'application/octet-stream'),
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Range',
                'Access-Control-Expose-Headers': 'Content-Range, Content-Length, Content-Type',
                'Cache-Control': 'public, max-age=3600',
            }
            if 'Content-Length' in resp.headers:
                resp_headers['Content-Length'] = resp.headers['Content-Length']
            if 'Content-Range' in resp.headers:
                resp_headers['Content-Range'] = resp.headers['Content-Range']
            if 'Accept-Ranges' in resp.headers:
                resp_headers['Accept-Ranges'] = resp.headers['Accept-Ranges']
            status = resp.status_code
            return Response(
                stream_with_context(resp.iter_content(chunk_size=65536)),
                status=status,
                headers=resp_headers
            )
        except Exception as e:
            logger.error(f"Media proxy error: {e}")
            return Response("Error fetching media", status=502)

class TeraboxDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Upgrade-Insecure-Requests': '1',
            'Connection': 'keep-alive',
        }
        self.base_url = "https://terabox.beer"
        self.cache = {}

    def extract_video_id(self, url):
        patterns = [
            r'/s/([a-zA-Z0-9_-]+)',
            r'share\.com/s/([a-zA-Z0-9_-]+)',
            r'file\.com/s/([a-zA-Z0-9_-]+)',
            r'terafileshare\.com/s/([a-zA-Z0-9_-]+)',
            r'terabox\.com/s/([a-zA-Z0-9_-]+)',
            r'1024terabox\.com/s/([a-zA-Z0-9_-]+)',
            r'teraboxapp\.com/s/([a-zA-Z0-9_-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def get_proxy_url(self, terabox_url):
        video_id = self.extract_video_id(terabox_url)
        if not video_id:
            return {"error": "Invalid or unsupported Terabox link. Please make sure you're using a valid Terabox share link."}

        cache_key = f"proxy_{terabox_url}"
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now() - cached['timestamp']).seconds < CACHE_DURATION:
                logger.info("Returning cached proxy URL")
                return cached['data']

        try:
            encoded_url = urllib.parse.quote(terabox_url, safe='')
            api_url = f"{self.base_url}/api/terabox-new?link={encoded_url}"
            
            response = self.session.get(api_url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                api_result = response.json()
                
                if isinstance(api_result, dict):
                    if api_result.get('error') and api_result.get('error') != False:
                        error_msg = api_result.get('error')
                        if isinstance(error_msg, str):
                            if "105" in error_msg:
                                return {"error": "The Terabox link is invalid or the video no longer exists. Please check the link and try again."}
                            elif "404" in error_msg:
                                return {"error": "Video not found. The link might be expired or removed."}
                            else:
                                return {"error": f"Terabox service error: {error_msg}. Please try again later."}
                        else:
                            return {"error": "Terabox service returned an error. Please try again later."}
                    
                    proxy_url = None
                    for field in ['proxy_url', 'download_link', 'fallback_url', 'stream_download_url']:
                        if field in api_result and api_result[field]:
                            proxy_url = api_result[field]
                            break
                    
                    if not proxy_url:
                        for key, value in api_result.items():
                            if isinstance(value, str) and value.startswith('http'):
                                if '.workers.dev' in value or 'proxy' in key.lower():
                                    proxy_url = value
                                    break
                    
                    if proxy_url:
                        result = {
                            "success": True,
                            "proxy_url": proxy_url,
                            "file_name": api_result.get('file_name', 'Unknown'),
                            "file_size": api_result.get('file_size', 'Unknown')
                        }
                        self.cache[cache_key] = {
                            'timestamp': datetime.now(),
                            'data': result
                        }
                        return result
                    else:
                        return {"error": "No video URL could be extracted from this Terabox link. The link might be private or unsupported."}
                else:
                    return {"error": "Invalid response from Terabox service. Please try again later."}
            elif response.status_code == 404:
                return {"error": "Terabox link not found. Please check if the link is correct."}
            elif response.status_code == 403:
                return {"error": "Access denied. The Terabox link might be private or restricted."}
            else:
                return {"error": f"Terabox service is currently unavailable (Status: {response.status_code}). Please try again later."}
                
        except requests.exceptions.Timeout:
            return {"error": "Connection to Terabox service timed out. Please try again."}
        except requests.exceptions.ConnectionError:
            return {"error": "Could not connect to Terabox service. Please check your internet connection."}
        except Exception as e:
            logger.error(f"Error: {str(e)}")
            return {"error": "An unexpected error occurred while processing the Terabox link. Please try again."}

    def process_terabox_link(self, terabox_url):
        result = self.get_proxy_url(terabox_url)
        if result.get('error'):
            return result
        
        proxy_url = result['proxy_url']
        logger.info(f"Proxy URL: {proxy_url[:100]}...")
        
        return {
            "success": True,
            "video_url": proxy_url,
            "file_name": result.get('file_name', 'Unknown'),
            "file_size": result.get('file_size', 'Unknown'),
            "platform": "terabox"
        }

faphouse_client = FaphouseClient()
terabox_client = TeraboxDownloader()

# ============= HTML TEMPLATES =============

MAIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Faphouse · Terabox Player</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@300;400;700;900&family=JetBrains+Mono:wght@300;400;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #000000;
            font-family: "Unbounded", sans-serif;
            color: #f5f0e6;
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0;
            margin: 0;
            overflow: hidden;
        }
        .app {
            width: 100%;
            height: 100vh;
            position: relative;
            overflow: hidden;
            background: #000000;
        }
        .splash-overlay {
            position: fixed;
            inset: 0;
            z-index: 1000;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: #000000;
            transition: opacity 1.2s ease, visibility 1.2s ease;
        }
        .splash-overlay.hidden { opacity: 0; visibility: hidden; pointer-events: none; }
        .splash-content {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 2.5rem;
        }
        .splash-18 {
            font-family: "Unbounded", sans-serif;
            font-size: 8rem;
            font-weight: 900;
            background: linear-gradient(135deg, #f5c518, #d4a800);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            line-height: 1;
            text-shadow: 0 0 80px rgba(245,197,24,0.05);
        }
        .splash-18 span {
            font-size: 3rem;
            display: block;
            font-weight: 300;
            letter-spacing: 0.3em;
            -webkit-text-fill-color: #3d3930;
            background: none;
            margin-top: 0.5rem;
        }
        .splash-btn {
            background: transparent;
            border: 2px solid rgba(245,197,24,0.1);
            padding: 0.8rem 3.5rem;
            font-family: "Unbounded", sans-serif;
            font-size: 0.7rem;
            color: #8a8477;
            cursor: pointer;
            transition: all 0.3s ease;
            letter-spacing: 0.3em;
            text-transform: uppercase;
            border-radius: 60px;
        }
        .splash-btn:hover {
            border-color: rgba(245,197,24,0.2);
            color: #f5f0e6;
            transform: scale(0.97);
        }
        .splash-sub {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.5rem;
            color: #1a1814;
            letter-spacing: 0.3em;
            text-transform: uppercase;
        }
        .page-paste {
            width: 100%;
            height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            position: relative;
            opacity: 0;
            transition: opacity 1.2s ease;
            padding: 2rem;
        }
        .page-paste.visible { opacity: 1; }
        .bg-glow {
            position: absolute;
            inset: 0;
            background: radial-gradient(ellipse at 50% 40%, rgba(245,197,24,0.02), transparent 70%);
            pointer-events: none;
            transition: background 0.6s ease;
        }
        .bg-glow.terabox-glow {
            background: radial-gradient(ellipse at 50% 40%, rgba(0,180,216,0.02), transparent 70%);
        }
        .bg-grid {
            position: absolute;
            inset: 0;
            background-image: 
                linear-gradient(rgba(255,215,0,0.008) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,215,0,0.008) 1px, transparent 1px);
            background-size: 60px 60px;
            pointer-events: none;
            transition: background-image 0.6s ease;
        }
        .bg-grid.terabox-grid {
            background-image: 
                linear-gradient(rgba(0,180,216,0.008) 1px, transparent 1px),
                linear-gradient(90deg, rgba(0,180,216,0.008) 1px, transparent 1px);
        }
        .brand-container {
            text-align: center;
            margin-bottom: 2.5rem;
            position: relative;
            min-height: 120px;
        }
        .logo-wrapper {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            min-height: 80px;
        }
        .logo-faphouse {
            display: flex;
            align-items: baseline;
            gap: 0.1rem;
            font-family: "Unbounded", sans-serif;
            font-size: 5rem;
            font-weight: 900;
            line-height: 1;
            letter-spacing: -0.02em;
            transition: all 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
            position: absolute;
            opacity: 0;
            transform: scale(0.8) rotate(-3deg);
            pointer-events: none;
        }
        .logo-faphouse.active {
            opacity: 1;
            transform: scale(1) rotate(0deg);
            pointer-events: auto;
            position: relative;
        }
        .logo-faphouse.hidden {
            opacity: 0;
            transform: scale(0.8) rotate(3deg);
            pointer-events: none;
            position: absolute;
        }
        .logo-faphouse .fap {
            background: linear-gradient(135deg, #f5c518, #d4a800);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            display: inline-block;
            animation: fapPulse 3s ease-in-out infinite;
        }
        @keyframes fapPulse {
            0%, 100% { filter: blur(0px); text-shadow: 0 0 40px rgba(245,197,24,0.03); transform: scale(1); }
            30% { filter: blur(5px); text-shadow: 0 0 60px rgba(245,197,24,0.08); transform: scale(1.02); }
            50% { filter: blur(0px); text-shadow: 0 0 40px rgba(245,197,24,0.03); transform: scale(1); }
            80% { filter: blur(5px); text-shadow: 0 0 60px rgba(245,197,24,0.08); transform: scale(1.02); }
        }
        .logo-faphouse .house {
            color: #f5f0e6;
            -webkit-text-fill-color: #f5f0e6;
            display: inline-block;
        }
        .logo-terabox {
            display: flex;
            align-items: baseline;
            gap: 0.1rem;
            font-family: "Unbounded", sans-serif;
            font-size: 4.2rem;
            font-weight: 900;
            line-height: 1;
            letter-spacing: -0.02em;
            transition: all 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
            position: absolute;
            opacity: 0;
            transform: scale(0.8) rotate(3deg);
            pointer-events: none;
        }
        .logo-terabox.active {
            opacity: 1;
            transform: scale(1) rotate(0deg);
            pointer-events: auto;
            position: relative;
        }
        .logo-terabox.hidden {
            opacity: 0;
            transform: scale(0.8) rotate(-3deg);
            pointer-events: none;
            position: absolute;
        }
        .logo-terabox .tera {
            background: linear-gradient(135deg, #00b4d8, #0077b6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            display: inline-block;
        }
        .logo-terabox .box-text {
            color: #f5f0e6;
            -webkit-text-fill-color: #f5f0e6;
            display: inline-block;
            animation: teraPulse 3.2s ease-in-out infinite;
        }
        @keyframes teraPulse {
            0%, 100% { filter: blur(0px); text-shadow: 0 0 40px rgba(0,180,216,0.03); transform: scale(1); }
            25% { filter: blur(4px); text-shadow: 0 0 60px rgba(0,180,216,0.08); transform: scale(0.9) rotate(-2deg); }
            45% { filter: blur(0px); text-shadow: 0 0 40px rgba(0,180,216,0.03); transform: scale(1.05) rotate(1deg); }
            65% { filter: blur(4px); text-shadow: 0 0 60px rgba(0,180,216,0.08); transform: scale(0.85) rotate(2deg); }
            85% { filter: blur(0px); text-shadow: 0 0 40px rgba(0,180,216,0.03); transform: scale(1) rotate(0deg); }
        }
        .badge-18 {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.55rem;
            font-weight: 700;
            color: #f5c518;
            background: rgba(245,197,24,0.04);
            border: 1px solid rgba(245,197,24,0.06);
            padding: 0.05rem 0.5rem;
            border-radius: 20px;
            display: inline-block;
            margin-left: 0.3rem;
            vertical-align: middle;
            -webkit-text-fill-color: #f5c518;
            letter-spacing: 0.05em;
            transition: all 0.6s ease;
        }
        .badge-18.terabox-badge {
            color: #00b4d8;
            border-color: rgba(0,180,216,0.06);
            -webkit-text-fill-color: #00b4d8;
            background: rgba(0,180,216,0.04);
        }
        .brand-tagline {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.55rem;
            color: #3d3930;
            letter-spacing: 0.3em;
            text-transform: uppercase;
            margin-top: 0.8rem;
            transition: color 0.6s ease;
        }
        .brand-tagline.terabox-tagline { color: #1a3a4a; }
        .platform-selector {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1.2rem;
            justify-content: center;
            background: rgba(255,255,255,0.01);
            padding: 0.3rem;
            border-radius: 60px;
            border: 1px solid rgba(255,255,255,0.02);
        }
        .platform-selector .pill {
            background: transparent;
            border: none;
            padding: 0.5rem 1.8rem;
            border-radius: 60px;
            font-family: "Unbounded", sans-serif;
            font-size: 0.6rem;
            color: #3d3930;
            cursor: pointer;
            transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            position: relative;
        }
        .platform-selector .pill:hover { color: #8a8477; }
        .platform-selector .pill.active-faphouse {
            color: #f5c518;
            background: rgba(245,197,24,0.06);
        }
        .platform-selector .pill.active-terabox {
            color: #00b4d8;
            background: rgba(0,180,216,0.06);
        }
        .platform-selector .pill::after {
            content: '';
            position: absolute;
            bottom: -1px;
            left: 50%;
            transform: translateX(-50%) scaleX(0);
            width: 60%;
            height: 2px;
            border-radius: 2px;
            transition: transform 0.4s ease;
        }
        .platform-selector .pill.active-faphouse::after {
            background: #f5c518;
            transform: translateX(-50%) scaleX(1);
        }
        .platform-selector .pill.active-terabox::after {
            background: #00b4d8;
            transform: translateX(-50%) scaleX(1);
        }
        .input-area {
            width: 100%;
            max-width: 720px;
            position: relative;
        }
        .input-wrapper {
            display: flex;
            align-items: center;
            background: rgba(8,8,8,0.9);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-radius: 80px;
            padding: 0.2rem 0.2rem 0.2rem 2rem;
            border: 1px solid rgba(255,215,0,0.03);
            transition: all 0.4s ease;
        }
        .input-wrapper:focus-within { border-color: rgba(255,215,0,0.06); }
        .input-wrapper.terabox-mode { border-color: rgba(0,180,216,0.03); }
        .input-wrapper.terabox-mode:focus-within { border-color: rgba(0,180,216,0.06); }
        .input-wrapper input {
            flex: 1;
            background: transparent;
            border: none;
            padding: 1rem 0.5rem 1rem 0;
            font-size: 0.8rem;
            font-family: "JetBrains Mono", monospace;
            color: #ece4d6;
            outline: none;
            font-weight: 300;
            min-width: 0;
        }
        .input-wrapper input::placeholder { color: #3a362e; font-weight: 200; }
        .input-wrapper .btn-load {
            background: #f5c518;
            border: none;
            padding: 0.8rem 2.5rem;
            border-radius: 60px;
            font-family: "Unbounded", sans-serif;
            font-weight: 700;
            font-size: 0.65rem;
            color: #000000;
            cursor: pointer;
            transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .input-wrapper .btn-load:hover { transform: scale(0.96); }
        .input-wrapper .btn-load:active { transform: scale(0.92); }
        .input-wrapper .btn-load.terabox-mode {
            background: #00b4d8;
        }
        .input-wrapper .btn-load.terabox-mode:hover {
            background: #48cae4;
        }
        .input-example {
            margin-top: 1rem;
            text-align: center;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.5rem;
            color: #3a362e;
        }
        .input-example .example-link {
            color: #6b6558;
            cursor: pointer;
            transition: color 0.3s ease;
            border-bottom: 1px solid rgba(255,215,0,0.02);
        }
        .input-example .example-link:hover { color: #c4bbaa; }
        .input-example .example-link.terabox-example {
            color: #0077b6;
        }
        .input-example .example-link.terabox-example:hover {
            color: #00b4d8;
        }
        .input-example .sep-dot { color: #1a1814; padding: 0 0.5rem; }
        
        /* Library Button */
        .library-toggle-btn {
            position: fixed;
            top: 1.5rem;
            right: 1.5rem;
            z-index: 50;
            background: rgba(255,255,255,0.01);
            border: 1px solid rgba(255,255,255,0.02);
            color: #3d3930;
            width: 44px;
            height: 44px;
            border-radius: 12px;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 4px;
            transition: all 0.3s ease;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.5rem;
        }
        .library-toggle-btn:hover {
            background: rgba(255,255,255,0.02);
            border-color: rgba(255,215,0,0.05);
        }
        .library-toggle-btn .bar {
            width: 20px;
            height: 2px;
            background: #3d3930;
            border-radius: 2px;
            transition: all 0.3s ease;
        }
        .library-toggle-btn .bar:nth-child(2) { width: 14px; }
        .library-toggle-btn .bar:nth-child(3) { width: 18px; }
        .library-toggle-btn .badge-count {
            position: absolute;
            top: -6px;
            right: -6px;
            background: rgba(245,197,24,0.1);
            color: #f5c518;
            font-size: 0.4rem;
            padding: 0.05rem 0.35rem;
            border-radius: 10px;
            border: 1px solid rgba(245,197,24,0.04);
            font-family: "JetBrains Mono", monospace;
            min-width: 16px;
            text-align: center;
        }
        .library-toggle-btn.terabox-mode .badge-count {
            color: #00b4d8;
            border-color: rgba(0,180,216,0.04);
        }
        
        /* Library Sidebar */
        .library-sidebar {
            position: fixed;
            top: 0;
            right: -400px;
            width: 380px;
            height: 100vh;
            background: rgba(8,8,8,0.98);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-left: 1px solid rgba(255,255,255,0.02);
            z-index: 60;
            transition: right 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
        }
        .library-sidebar.open {
            right: 0;
        }
        .library-sidebar-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.5);
            z-index: 55;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }
        .library-sidebar-backdrop.open {
            opacity: 1;
            pointer-events: auto;
        }
        .library-sidebar-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 1rem;
            border-bottom: 1px solid rgba(255,255,255,0.02);
            margin-bottom: 1rem;
            flex-shrink: 0;
        }
        .library-sidebar-title {
            font-family: "Unbounded", sans-serif;
            font-size: 0.8rem;
            font-weight: 700;
            color: #3d3930;
            letter-spacing: 0.1em;
            text-transform: uppercase;
        }
        .library-sidebar-title .count {
            color: #1a1814;
            font-weight: 300;
            font-size: 0.6rem;
        }
        .library-sidebar-close {
            background: transparent;
            border: none;
            color: #3a362e;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 0.2rem 0.5rem;
            transition: color 0.3s ease;
        }
        .library-sidebar-close:hover {
            color: #8a8477;
        }
        .library-sidebar-actions {
            display: flex;
            gap: 0.5rem;
            flex-shrink: 0;
            margin-bottom: 0.8rem;
        }
        .library-sidebar-actions button {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.4rem;
            color: #3a362e;
            background: transparent;
            border: 1px solid rgba(255,255,255,0.02);
            padding: 0.3rem 0.8rem;
            border-radius: 20px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .library-sidebar-actions button:hover {
            color: #6b6558;
            border-color: rgba(255,255,255,0.04);
        }
        .library-sidebar-actions button.clear:hover {
            color: #ff4444;
            border-color: rgba(255,68,68,0.1);
        }
        .library-list {
            flex: 1;
            overflow-y: auto;
            padding-right: 0.5rem;
        }
        .library-list::-webkit-scrollbar {
            width: 3px;
        }
        .library-list::-webkit-scrollbar-track {
            background: rgba(255,255,255,0.01);
        }
        .library-list::-webkit-scrollbar-thumb {
            background: rgba(255,215,0,0.1);
            border-radius: 3px;
        }
        .library-item {
            background: rgba(255,255,255,0.01);
            border: 1px solid rgba(255,255,255,0.02);
            border-radius: 8px;
            padding: 0.6rem 0.8rem;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.5rem;
            margin-bottom: 0.3rem;
        }
        .library-item:hover {
            background: rgba(255,255,255,0.03);
            border-color: rgba(255,215,0,0.05);
            transform: translateX(2px);
        }
        .library-item .item-info {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            flex: 1;
            min-width: 0;
        }
        .library-item .item-icon {
            font-size: 0.9rem;
            flex-shrink: 0;
        }
        .library-item .item-title {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.6rem;
            color: #6b6558;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            flex: 1;
        }
        .library-item .item-platform {
            padding: 0.05rem 0.4rem;
            border-radius: 10px;
            font-size: 0.35rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            flex-shrink: 0;
        }
        .library-item .item-platform.faphouse {
            background: rgba(245,197,24,0.04);
            color: #f5c518;
            border: 1px solid rgba(245,197,24,0.04);
        }
        .library-item .item-platform.terabox {
            background: rgba(0,180,216,0.04);
            color: #00b4d8;
            border: 1px solid rgba(0,180,216,0.04);
        }
        .library-item .item-remove {
            background: transparent;
            border: none;
            color: #3a362e;
            font-size: 0.6rem;
            cursor: pointer;
            transition: all 0.3s ease;
            padding: 0 0.2rem;
            flex-shrink: 0;
        }
        .library-item .item-remove:hover {
            color: #ff4444;
        }
        .library-empty {
            text-align: center;
            padding: 2rem 0;
            color: #1a1814;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.6rem;
            letter-spacing: 0.1em;
        }
        .library-empty .empty-icon {
            font-size: 2rem;
            display: block;
            margin-bottom: 0.5rem;
        }
        
        .paste-footer {
            position: fixed;
            bottom: 1.5rem;
            left: 0;
            right: 0;
            text-align: center;
            z-index: 10;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.45rem;
            color: #1a1814;
            letter-spacing: 0.3em;
            text-transform: uppercase;
            transition: color 0.6s ease;
        }
        .paste-footer.terabox-footer { color: #0a2a3a; }
        @media (max-width: 900px) {
            .logo-faphouse { font-size: 3.5rem; }
            .logo-terabox { font-size: 3rem; }
            .platform-selector .pill { padding: 0.4rem 1.2rem; font-size: 0.5rem; }
            .input-wrapper { flex-wrap: wrap; background: transparent; padding: 0; border: none; backdrop-filter: none; }
            .input-wrapper input { padding: 0.8rem 1.2rem; background: rgba(8,8,8,0.9); border-radius: 60px; border: 1px solid rgba(255,215,0,0.03); width: 100%; margin-bottom: 0.5rem; }
            .input-wrapper .btn-load { width: 100%; justify-content: center; }
            .splash-18 { font-size: 5rem; }
            .badge-18 { font-size: 0.45rem; padding: 0.02rem 0.4rem; }
            .library-sidebar {
                width: 320px;
                right: -340px;
            }
            .library-sidebar.open { right: 0; }
        }
        @media (max-width: 500px) {
            .logo-faphouse { font-size: 2.4rem; }
            .logo-terabox { font-size: 2.2rem; }
            .splash-18 { font-size: 3.5rem; }
            .splash-18 span { font-size: 1.5rem; }
            .badge-18 { font-size: 0.4rem; padding: 0.02rem 0.3rem; }
            .platform-selector { gap: 0.3rem; padding: 0.2rem; }
            .platform-selector .pill { padding: 0.3rem 0.8rem; font-size: 0.4rem; }
            .library-sidebar {
                width: 100%;
                right: -100%;
            }
            .library-sidebar.open { right: 0; }
            .library-toggle-btn {
                top: 1rem;
                right: 1rem;
                width: 38px;
                height: 38px;
            }
            .library-toggle-btn .bar { width: 16px; }
            .library-toggle-btn .bar:nth-child(2) { width: 12px; }
            .library-toggle-btn .bar:nth-child(3) { width: 14px; }
        }
    </style>
</head>
<body>
<div class="app" id="app">
    <div class="splash-overlay" id="splashOverlay">
        <div class="splash-content">
            <div class="splash-18">18+<span>adult content</span></div>
            <button class="splash-btn" id="enterBtn">enter</button>
            <div class="splash-sub">you must be 18 or older to continue</div>
        </div>
    </div>
    <div class="page-paste" id="pagePaste">
        <div class="bg-glow" id="bgGlow"></div>
        <div class="bg-grid" id="bgGrid"></div>
        <div class="brand-container">
            <div class="logo-wrapper" id="logoWrapper">
                <div class="logo-faphouse active" id="logoFaphouse">
                    <span class="fap">FAP</span>
                    <span class="house">HOUSE</span>
                    <span class="badge-18" id="badgeFaphouse">18+</span>
                </div>
                <div class="logo-terabox hidden" id="logoTerabox">
                    <span class="tera">TERA</span>
                    <span class="box-text">BOX</span>
                    <span class="badge-18 terabox-badge" id="badgeTerabox">18+</span>
                </div>
            </div>
            <div class="brand-tagline" id="brandTagline">player · zero latency · dual platform</div>
        </div>
        <div class="input-area">
            <div class="platform-selector" id="platformSelector">
                <button class="pill active-faphouse" data-platform="faphouse" id="faphousePill">Faphouse</button>
                <button class="pill" data-platform="terabox" id="teraboxPill">Terabox</button>
            </div>
            <form method="GET" action="/play" style="width:100%;" id="urlForm">
                <div class="input-wrapper" id="inputWrapper">
                    <input type="text" name="url" id="videoUrlInput" placeholder="https://faphouse2.com/videos/..." spellcheck="false" value="{{ video_url or '' }}">
                    <button type="submit" class="btn-load" id="loadBtn">load</button>
                </div>
            </form>
            <div class="input-example">
                <span>try </span>
                <span class="example-link" id="exampleFaphouse">https://faphouse2.com/videos/shared-bed-stepsister-fuck-C6Qi1u</span>
                <span class="sep-dot">·</span>
                <span class="example-link terabox-example" id="exampleTerabox">https://terafileshare.com/s/1xJtL3j2LJ-ZsUA6zbG7Pug</span>
            </div>
        </div>
        
        <div class="paste-footer" id="pasteFooter">premium · yellow black · faphouse + terabox</div>
    </div>
    
    <!-- Library Toggle Button -->
    <button class="library-toggle-btn" id="libraryToggleBtn">
        <span class="bar"></span>
        <span class="bar"></span>
        <span class="bar"></span>
        <span class="badge-count" id="libraryBadge">0</span>
    </button>
    
    <!-- Library Backdrop -->
    <div class="library-sidebar-backdrop" id="libraryBackdrop"></div>
    
    <!-- Library Sidebar -->
    <div class="library-sidebar" id="librarySidebar">
        <div class="library-sidebar-header">
            <div class="library-sidebar-title">
                📚 Library <span class="count" id="sidebarCount">(0)</span>
            </div>
            <button class="library-sidebar-close" id="libraryCloseBtn">✕</button>
        </div>
        <div class="library-sidebar-actions">
            <button id="refreshLibraryBtn">↻ refresh</button>
            <button class="clear" id="clearLibraryBtn">clear all</button>
        </div>
        <div class="library-list" id="libraryList">
            <div class="library-empty">
                <span class="empty-icon">🎬</span>
                No videos in library yet<br>
                Watch something to save it here
            </div>
        </div>
    </div>
</div>
<script>
    // ===== LIBRARY FUNCTIONS =====
    function getLibrary(platform) {
        try {
            const key = platform === 'faphouse' ? 'faphouseLibrary' : 'teraboxLibrary';
            return JSON.parse(localStorage.getItem(key) || '[]');
        } catch {
            return [];
        }
    }
    
    function saveLibrary(platform, library) {
        const key = platform === 'faphouse' ? 'faphouseLibrary' : 'teraboxLibrary';
        localStorage.setItem(key, JSON.stringify(library));
        renderLibrary();
    }
    
    function addToLibrary(platform, video) {
        const library = getLibrary(platform);
        const exists = library.some(item => item.url === video.url);
        if (!exists) {
            video.watchedAt = new Date().toISOString();
            library.unshift(video);
            saveLibrary(platform, library);
            showToast('📚 Added to ' + platform + ' library');
            return true;
        }
        return false;
    }
    
    function removeFromLibrary(platform, url) {
        const library = getLibrary(platform).filter(item => item.url !== url);
        saveLibrary(platform, library);
    }
    
    function clearLibrary(platform) {
        if (confirm('Clear all ' + platform + ' videos from library?')) {
            saveLibrary(platform, []);
        }
    }
    
    function showToast(message) {
        const toast = document.getElementById('saveToast');
        if (!toast) {
            const newToast = document.createElement('div');
            newToast.id = 'saveToast';
            newToast.style.cssText = `
                position: fixed;
                bottom: 80px;
                left: 50%;
                transform: translateX(-50%);
                background: rgba(245,197,24,0.1);
                border: 1px solid rgba(245,197,24,0.05);
                padding: 0.4rem 1.2rem;
                border-radius: 30px;
                font-family: "JetBrains Mono", monospace;
                font-size: 0.5rem;
                color: #f5c518;
                opacity: 0;
                transition: all 0.5s ease;
                pointer-events: none;
                z-index: 100;
                backdrop-filter: blur(10px);
            `;
            document.body.appendChild(newToast);
            newToast.textContent = message;
            setTimeout(() => {
                newToast.classList.add('show');
                newToast.style.opacity = '1';
                newToast.style.bottom = '100px';
            }, 100);
            setTimeout(() => {
                newToast.style.opacity = '0';
                newToast.style.bottom = '80px';
                setTimeout(() => newToast.remove(), 500);
            }, 3000);
            return;
        }
        toast.textContent = message;
        toast.classList.add('show');
        setTimeout(() => {
            toast.classList.remove('show');
        }, 3000);
    }
    
    function renderLibrary() {
        const platform = currentPlatform || 'faphouse';
        const library = getLibrary(platform);
        const list = document.getElementById('libraryList');
        const badge = document.getElementById('libraryBadge');
        const sidebarCount = document.getElementById('sidebarCount');
        const btn = document.getElementById('libraryToggleBtn');
        
        badge.textContent = library.length;
        sidebarCount.textContent = `(${library.length})`;
        
        if (platform === 'terabox') {
            btn.classList.add('terabox-mode');
        } else {
            btn.classList.remove('terabox-mode');
        }
        
        if (library.length === 0) {
            list.innerHTML = `
                <div class="library-empty">
                    <span class="empty-icon">🎬</span>
                    No ${platform} videos in library yet
                </div>
            `;
            return;
        }
        
        let html = '';
        library.forEach((item, index) => {
            const icon = platform === 'faphouse' ? '🔞' : '📦';
            const title = item.title || (item.file_name || 'Untitled');
            
            html += `
                <div class="library-item" data-index="${index}" data-url="${item.url}">
                    <div class="item-info">
                        <span class="item-icon">${icon}</span>
                        <span class="item-title">${title}</span>
                        <span class="item-platform ${platform}">${platform}</span>
                    </div>
                    <button class="item-remove" data-url="${item.url}">✕</button>
                </div>
            `;
        });
        
        list.innerHTML = html;
        
        list.querySelectorAll('.library-item').forEach(el => {
            const url = el.dataset.url;
            
            el.addEventListener('click', function(e) {
                if (e.target.closest('.item-remove')) return;
                const form = document.getElementById('urlForm');
                const input = document.getElementById('videoUrlInput');
                input.value = url;
                form.action = platform === 'faphouse' ? '/play' : '/terabox';
                form.submit();
                closeLibrary();
            });
            
            el.querySelector('.item-remove').addEventListener('click', function(e) {
                e.stopPropagation();
                removeFromLibrary(platform, this.dataset.url);
            });
        });
    }
    
    // ===== LIBRARY SIDEBAR CONTROLS =====
    function toggleLibrary() {
        const sidebar = document.getElementById('librarySidebar');
        const backdrop = document.getElementById('libraryBackdrop');
        sidebar.classList.toggle('open');
        backdrop.classList.toggle('open');
        if (sidebar.classList.contains('open')) {
            renderLibrary();
        }
    }
    
    function closeLibrary() {
        document.getElementById('librarySidebar').classList.remove('open');
        document.getElementById('libraryBackdrop').classList.remove('open');
    }
    
    document.getElementById('libraryToggleBtn').addEventListener('click', toggleLibrary);
    document.getElementById('libraryCloseBtn').addEventListener('click', closeLibrary);
    document.getElementById('libraryBackdrop').addEventListener('click', closeLibrary);
    document.getElementById('refreshLibraryBtn').addEventListener('click', renderLibrary);
    document.getElementById('clearLibraryBtn').addEventListener('click', function() {
        clearLibrary(currentPlatform);
    });
    
    // ===== UI FUNCTIONS =====
    document.getElementById('enterBtn').addEventListener('click', function() {
        document.getElementById('splashOverlay').classList.add('hidden');
        document.getElementById('pagePaste').classList.add('visible');
        renderLibrary();
    });
    
    const faphousePill = document.getElementById('faphousePill');
    const teraboxPill = document.getElementById('teraboxPill');
    const logoFaphouse = document.getElementById('logoFaphouse');
    const logoTerabox = document.getElementById('logoTerabox');
    const badgeFaphouse = document.getElementById('badgeFaphouse');
    const badgeTerabox = document.getElementById('badgeTerabox');
    const brandTagline = document.getElementById('brandTagline');
    const pasteFooter = document.getElementById('pasteFooter');
    const bgGlow = document.getElementById('bgGlow');
    const bgGrid = document.getElementById('bgGrid');
    const inputWrapper = document.getElementById('inputWrapper');
    const loadBtn = document.getElementById('loadBtn');
    const videoUrlInput = document.getElementById('videoUrlInput');
    const urlForm = document.getElementById('urlForm');
    const exampleFaphouse = document.getElementById('exampleFaphouse');
    const exampleTerabox = document.getElementById('exampleTerabox');
    
    let currentPlatform = 'faphouse';
    
    function setPlatform(platform) {
        currentPlatform = platform;
        faphousePill.classList.remove('active-faphouse');
        teraboxPill.classList.remove('active-terabox');
        
        if (platform === 'faphouse') {
            faphousePill.classList.add('active-faphouse');
            logoFaphouse.classList.remove('hidden');
            logoFaphouse.classList.add('active');
            logoTerabox.classList.remove('active');
            logoTerabox.classList.add('hidden');
            badgeFaphouse.classList.remove('terabox-badge');
            brandTagline.classList.remove('terabox-tagline');
            pasteFooter.classList.remove('terabox-footer');
            bgGlow.classList.remove('terabox-glow');
            bgGrid.classList.remove('terabox-grid');
            inputWrapper.classList.remove('terabox-mode');
            loadBtn.classList.remove('terabox-mode');
            videoUrlInput.placeholder = 'https://faphouse2.com/videos/...';
            loadBtn.textContent = 'load';
            urlForm.action = '/play';
        } else {
            teraboxPill.classList.add('active-terabox');
            logoTerabox.classList.remove('hidden');
            logoTerabox.classList.add('active');
            logoFaphouse.classList.remove('active');
            logoFaphouse.classList.add('hidden');
            badgeTerabox.classList.add('terabox-badge');
            brandTagline.classList.add('terabox-tagline');
            pasteFooter.classList.add('terabox-footer');
            bgGlow.classList.add('terabox-glow');
            bgGrid.classList.add('terabox-grid');
            inputWrapper.classList.add('terabox-mode');
            loadBtn.classList.add('terabox-mode');
            videoUrlInput.placeholder = 'https://terafileshare.com/s/...';
            loadBtn.textContent = 'extract';
            urlForm.action = '/terabox';
        }
        renderLibrary();
    }
    
    function detectPlatformFromUrl(val) {
        if (val.includes('terabox') || 
            val.includes('terafileshare') ||
            val.includes('share.com') || 
            val.includes('file.com') ||
            val.includes('teraboxlink') ||
            val.includes('1024terabox') ||
            val.includes('teraboxapp')) {
            setPlatform('terabox');
        } else if (val.includes('faphouse') || val.includes('faphouse2')) {
            setPlatform('faphouse');
        }
    }
    
    faphousePill.addEventListener('click', function() { 
        setPlatform('faphouse'); 
        videoUrlInput.value = '';
    });
    
    teraboxPill.addEventListener('click', function() { 
        setPlatform('terabox');
        videoUrlInput.value = '';
    });
    
    exampleFaphouse.addEventListener('click', function() {
        setPlatform('faphouse');
        videoUrlInput.value = this.textContent;
        detectPlatformFromUrl(this.textContent.toLowerCase());
        setTimeout(function() {
            urlForm.submit();
        }, 100);
    });
    
    exampleTerabox.addEventListener('click', function() {
        setPlatform('terabox');
        videoUrlInput.value = this.textContent;
        detectPlatformFromUrl(this.textContent.toLowerCase());
        setTimeout(function() {
            urlForm.submit();
        }, 100);
    });
    
    videoUrlInput.addEventListener('paste', function(e) {
        setTimeout(function() {
            const val = this.value.toLowerCase();
            detectPlatformFromUrl(val);
        }.bind(this), 50);
    });
    
    videoUrlInput.addEventListener('input', function() {
        const val = this.value.toLowerCase();
        detectPlatformFromUrl(val);
    });
    
    videoUrlInput.addEventListener('change', function() {
        const val = this.value.toLowerCase();
        detectPlatformFromUrl(val);
    });
    
    videoUrlInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            const val = this.value.toLowerCase();
            detectPlatformFromUrl(val);
            urlForm.submit();
        }
    });
</script>
</body>
</html>
"""

PLAYER_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>Faphouse Player</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@300;400;700;900&family=JetBrains+Mono:wght@300;400;700&display=swap" rel="stylesheet">
    <link href="https://vjs.zencdn.net/8.0.0/video-js.css" rel="stylesheet" />
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body {
            background: #0a0a0a;
            font-family: "Unbounded", sans-serif;
            color: #f5f0e6;
            height: 100vh;
            width: 100vw;
            overflow: hidden;
            position: fixed;
            top: 0;
            left: 0;
            margin: 0;
            padding: 0;
        }
        .app {
            width: 100vw;
            height: 100vh;
            position: relative;
            background: #0a0a0a;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }
        .video-wrapper {
            position: relative;
            width: 90%;
            max-width: 900px;
            aspect-ratio: 16/9;
            background: #000000;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 0 0 1px rgba(255,215,0,0.02), 0 20px 60px rgba(0,0,0,0.9);
        }
        #player {
            width: 100%;
            height: 100%;
            display: block;
            background: #000000;
        }
        .header {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            z-index: 15;
            padding: 1rem 1.5rem;
            background: linear-gradient(180deg, rgba(0,0,0,0.7) 0%, transparent 100%);
            display: flex;
            align-items: center;
            justify-content: space-between;
            opacity: 0;
            transition: opacity 0.3s ease;
            pointer-events: none;
        }
        .header.visible { opacity: 1; pointer-events: auto; }
        .header-brand { display: flex; align-items: baseline; gap: 0.2rem; }
        .header-brand .fap {
            font-family: "Unbounded", sans-serif;
            font-size: 0.9rem;
            font-weight: 900;
            background: linear-gradient(135deg, #f5c518, #d4a800);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .header-brand .house {
            font-family: "Unbounded", sans-serif;
            font-size: 0.9rem;
            font-weight: 900;
            color: #f5f0e6;
        }
        .header-badge {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.4rem;
            font-weight: 700;
            color: #f5c518;
            background: rgba(245,197,24,0.04);
            border: 1px solid rgba(245,197,24,0.06);
            padding: 0.02rem 0.4rem;
            border-radius: 20px;
            letter-spacing: 0.05em;
            margin-left: 0.2rem;
        }
        .header-status {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.35rem;
            color: rgba(255,255,255,0.2);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            display: flex;
            align-items: center;
            gap: 0.3rem;
        }
        .header-status .dot {
            width: 4px;
            height: 4px;
            border-radius: 50%;
            background: #f5c518;
            animation: pulse 1.5s infinite;
            display: inline-block;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.2; } }
        .back-btn {
            background: rgba(255,255,255,0.02);
            border: 1px solid rgba(255,255,255,0.03);
            color: rgba(255,255,255,0.3);
            padding: 0.15rem 0.8rem;
            border-radius: 30px;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.4rem;
            cursor: pointer;
            transition: all 0.2s ease;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            text-decoration: none;
            touch-action: manipulation;
            min-height: 24px;
            display: flex;
            align-items: center;
        }
        .back-btn:hover { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.6); }
        .center-play {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 12;
            width: 64px;
            height: 64px;
            border-radius: 50%;
            background: rgba(0,0,0,0.5);
            border: 2px solid rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.6);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s ease;
            opacity: 0;
            pointer-events: none;
        }
        .center-play.visible { opacity: 1; pointer-events: auto; }
        .center-play:hover {
            background: rgba(255,255,255,0.05);
            border-color: rgba(255,215,0,0.1);
            transform: translate(-50%, -50%) scale(1.05);
        }
        .center-play:active { transform: translate(-50%, -50%) scale(0.92); }
        .center-play svg { width: 28px; height: 28px; fill: currentColor; margin-left: 4px; }
        .controls-wrapper {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 20;
            padding: 0 1.2rem 1.2rem 1.2rem;
            background: linear-gradient(0deg, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.1) 70%, transparent 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
        }
        .controls-wrapper.visible { opacity: 1; }
        .progress-section { width: 100%; padding: 0.3rem 0 0.2rem 0; }
        .progress-track {
            position: relative;
            width: 100%;
            height: 3px;
            background: rgba(255,255,255,0.1);
            border-radius: 2px;
            cursor: pointer;
            transition: height 0.2s ease;
        }
        .progress-track:hover { height: 5px; }
        .progress-fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #f5c518, #d4a800);
            border-radius: 2px;
            position: relative;
            transition: width 0.1s ease;
        }
        .progress-fill::after {
            content: '';
            position: absolute;
            right: -4px;
            top: -3px;
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background: #f5c518;
            opacity: 0;
            transition: opacity 0.2s ease;
            box-shadow: 0 0 15px rgba(245,197,24,0.2);
        }
        .progress-track:hover .progress-fill::after,
        .progress-track.touching .progress-fill::after { opacity: 1; }
        .controls-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.2rem 0;
            gap: 0.3rem;
        }
        .controls-row button {
            background: transparent;
            border: none;
            color: rgba(255,255,255,0.5);
            padding: 0.2rem 0.4rem;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.55rem;
            cursor: pointer;
            transition: all 0.15s ease;
            letter-spacing: 0.02em;
            border-radius: 30px;
            min-height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            touch-action: manipulation;
        }
        .controls-row button:active { transform: scale(0.92); color: #ffffff; }
        .controls-row .play-btn {
            font-family: "Unbounded", sans-serif;
            font-size: 0.6rem;
            color: #ffffff;
            padding: 0.2rem 1.2rem;
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 30px;
            min-width: 54px;
            background: rgba(255,255,255,0.01);
        }
        .controls-row .play-btn:hover {
            background: rgba(255,255,255,0.03);
            border-color: rgba(255,255,255,0.06);
        }
        .controls-row .play-btn:active {
            background: rgba(255,215,0,0.04);
            border-color: rgba(255,215,0,0.06);
            transform: scale(0.95);
        }
        .controls-row .seek-btn {
            font-size: 0.45rem;
            color: rgba(255,255,255,0.3);
            padding: 0.15rem 0.3rem;
            min-height: 24px;
        }
        .controls-row .seek-btn:hover { color: rgba(255,255,255,0.7); }
        .controls-row .time-display {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.45rem;
            color: rgba(255,255,255,0.25);
            padding: 0.1rem 0.3rem;
            letter-spacing: 0.02em;
            min-width: 60px;
            text-align: center;
            font-variant-numeric: tabular-nums;
        }
        .controls-row .fs-btn {
            font-size: 0.45rem;
            color: rgba(255,255,255,0.25);
            padding: 0.15rem 0.4rem;
            letter-spacing: 0.05em;
            min-height: 24px;
        }
        .controls-row .fs-btn:hover { color: rgba(255,255,255,0.6); }
        .click-overlay {
            position: absolute;
            inset: 0;
            z-index: 10;
            cursor: pointer;
        }
        .save-toast {
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(245,197,24,0.1);
            border: 1px solid rgba(245,197,24,0.05);
            padding: 0.4rem 1.2rem;
            border-radius: 30px;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.5rem;
            color: #f5c518;
            opacity: 0;
            transition: all 0.5s ease;
            pointer-events: none;
            z-index: 100;
            backdrop-filter: blur(10px);
        }
        .save-toast.show {
            opacity: 1;
            bottom: 100px;
        }
        @media (max-width: 700px) {
            .video-wrapper { width: 96%; border-radius: 8px; }
            .header { padding: 0.6rem 1rem; }
            .header-brand .fap, .header-brand .house { font-size: 0.75rem; }
            .header-badge { font-size: 0.35rem; padding: 0.02rem 0.3rem; }
            .controls-wrapper { padding: 0 0.8rem 0.8rem 0.8rem; }
            .controls-row button { font-size: 0.45rem; min-height: 24px; padding: 0.15rem 0.3rem; }
            .controls-row .play-btn { font-size: 0.5rem; padding: 0.15rem 0.8rem; min-width: 44px; }
            .controls-row .time-display { font-size: 0.38rem; min-width: 50px; }
            .controls-row .seek-btn { font-size: 0.38rem; }
            .controls-row .fs-btn { font-size: 0.38rem; }
            .center-play { width: 50px; height: 50px; }
            .center-play svg { width: 22px; height: 22px; }
            .back-btn { font-size: 0.35rem; padding: 0.1rem 0.6rem; min-height: 20px; }
            .progress-section { padding: 0.2rem 0 0.1rem 0; }
            .save-toast { font-size: 0.45rem; padding: 0.3rem 0.8rem; bottom: 60px; }
            .save-toast.show { bottom: 80px; }
        }
        @media (max-width: 450px) {
            .center-play { width: 44px; height: 44px; }
            .center-play svg { width: 18px; height: 18px; }
            .controls-row .play-btn { font-size: 0.45rem; padding: 0.12rem 0.6rem; min-width: 38px; }
            .controls-row .time-display { font-size: 0.35rem; min-width: 44px; }
        }
        @media (orientation: landscape) and (max-height: 500px) {
            .video-wrapper { width: 85%; max-height: 85vh; }
            .header { padding: 0.4rem 1rem; }
            .header-brand .fap, .header-brand .house { font-size: 0.7rem; }
            .controls-wrapper { padding: 0 1rem 0.6rem 1rem; }
            .controls-row button { font-size: 0.4rem; min-height: 20px; padding: 0.1rem 0.25rem; }
            .controls-row .play-btn { font-size: 0.45rem; padding: 0.1rem 0.6rem; min-width: 36px; }
            .controls-row .time-display { font-size: 0.35rem; min-width: 40px; }
            .center-play { width: 40px; height: 40px; }
            .center-play svg { width: 16px; height: 16px; }
            .back-btn { font-size: 0.3rem; padding: 0.1rem 0.4rem; min-height: 16px; }
            .progress-section { padding: 0.15rem 0 0.05rem 0; }
            .progress-track { height: 2px; }
        }
    </style>
</head>
<body>
<div class="app">
    <div class="video-wrapper" id="videoWrapper">
        <video id="player" class="video-js vjs-default-skin" controls autoplay preload="auto" style="width:100%;height:100%;">
            <source src="{{ m3u8_url }}" type="application/x-mpegURL">
        </video>
        <div class="header" id="header">
            <div class="header-brand">
                <span class="fap">FAP</span>
                <span class="house">HOUSE</span>
                <span class="header-badge">18+</span>
            </div>
            <div style="display:flex; align-items:center; gap:0.6rem;">
                <span class="header-status"><span class="dot"></span> live</span>
                <a href="/" class="back-btn">back</a>
            </div>
        </div>
        <button class="center-play" id="centerPlayBtn">
            <svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>
        </button>
        <div class="click-overlay" id="clickOverlay"></div>
        <div class="controls-wrapper" id="controlsWrapper">
            <div class="progress-section">
                <div class="progress-track" id="progressTrack">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
            </div>
            <div class="controls-row">
                <button class="seek-btn" id="seekBack">-10</button>
                <button class="play-btn" id="playPauseBtn">play</button>
                <span class="time-display" id="timeDisplay">0:00 / 0:00</span>
                <button class="seek-btn" id="seekForward">+10</button>
                <button class="fs-btn" id="fullscreenBtn">full</button>
            </div>
        </div>
    </div>
</div>

<div class="save-toast" id="saveToast">📚 Added to library</div>

<script src="https://vjs.zencdn.net/8.0.0/video.min.js"></script>
<script>
    document.addEventListener('DOMContentLoaded', function() {
        // ===== LIBRARY FUNCTIONS =====
        function getLibrary(platform) {
            try {
                const key = platform === 'faphouse' ? 'faphouseLibrary' : 'teraboxLibrary';
                return JSON.parse(localStorage.getItem(key) || '[]');
            } catch {
                return [];
            }
        }
        
        function saveLibrary(platform, library) {
            const key = platform === 'faphouse' ? 'faphouseLibrary' : 'teraboxLibrary';
            localStorage.setItem(key, JSON.stringify(library));
        }
        
        function addToLibrary(platform, video) {
            const library = getLibrary(platform);
            const exists = library.some(item => item.url === video.url);
            if (!exists) {
                video.watchedAt = new Date().toISOString();
                library.unshift(video);
                saveLibrary(platform, library);
                showToast('📚 Added to ' + platform + ' library');
                return true;
            }
            return false;
        }
        
        function showToast(message) {
            const toast = document.getElementById('saveToast');
            toast.textContent = message;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }
        
        // ===== SAVE VIDEO TO LIBRARY =====
        const videoUrl = "{{ m3u8_url }}";
        const originalUrl = new URLSearchParams(window.location.search).get('url') || '';
        
        let videoTitle = '';
        if (originalUrl) {
            const match = originalUrl.match(/videos\/([^\/?]+)/);
            if (match) {
                videoTitle = match[1].replace(/-/g, ' ').replace(/_/g, ' ');
            }
        }
        if (!videoTitle || videoTitle.length < 3) {
            videoTitle = 'Faphouse Video';
        }
        
        let saved = false;
        
        var player = videojs('player', {
            html5: { hls: { enableLowInitialPlaylist: true, smoothQualityChange: true, overrideNative: true } },
            controls: false,
            autoplay: true,
            preload: 'auto'
        });
        
        player.on('play', function() {
            if (!saved && videoUrl) {
                saved = true;
                addToLibrary('faphouse', {
                    url: originalUrl || videoUrl,
                    title: videoTitle,
                    platform: 'faphouse',
                    videoUrl: videoUrl
                });
            }
        });
        
        setTimeout(function() {
            if (!saved && videoUrl) {
                saved = true;
                addToLibrary('faphouse', {
                    url: originalUrl || videoUrl,
                    title: videoTitle,
                    platform: 'faphouse',
                    videoUrl: videoUrl
                });
            }
        }, 5000);
        
        // ===== PLAYER CONTROLS =====
        const centerPlayBtn = document.getElementById('centerPlayBtn');
        const playPauseBtn = document.getElementById('playPauseBtn');
        const seekBack = document.getElementById('seekBack');
        const seekForward = document.getElementById('seekForward');
        const timeDisplay = document.getElementById('timeDisplay');
        const fullscreenBtn = document.getElementById('fullscreenBtn');
        const progressFill = document.getElementById('progressFill');
        const progressTrack = document.getElementById('progressTrack');
        const controlsWrapper = document.getElementById('controlsWrapper');
        const header = document.getElementById('header');
        const clickOverlay = document.getElementById('clickOverlay');
        const videoWrapper = document.getElementById('videoWrapper');
        
        function formatTime(seconds) {
            if (isNaN(seconds) || !isFinite(seconds)) return '0:00';
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            return m + ':' + s.toString().padStart(2, '0');
        }
        
        function updateTimeDisplay() {
            const currentTime = player.currentTime();
            const duration = player.duration();
            if (duration) {
                timeDisplay.textContent = formatTime(currentTime) + ' / ' + formatTime(duration);
                progressFill.style.width = ((currentTime / duration) * 100) + '%';
            } else {
                timeDisplay.textContent = '0:00 / 0:00';
                progressFill.style.width = '0%';
            }
        }
        
        function toggleControls(show) {
            controlsWrapper.classList.toggle('visible', show);
            header.classList.toggle('visible', show);
        }
        
        function toggleCenterPlay(show) {
            centerPlayBtn.classList.toggle('visible', show);
        }
        
        let controlsVisible = true;
        let controlsTimeout;
        
        function showControls() {
            toggleControls(true);
            controlsVisible = true;
            clearTimeout(controlsTimeout);
        }
        
        function hideControlsDelayed() {
            clearTimeout(controlsTimeout);
            controlsTimeout = setTimeout(function() {
                if (!player.paused()) {
                    toggleControls(false);
                    controlsVisible = false;
                }
            }, 3000);
        }
        
        function togglePlayPause() {
            if (player.paused()) {
                player.play();
                playPauseBtn.textContent = 'pause';
                centerPlayBtn.classList.remove('visible');
                if (controlsVisible) hideControlsDelayed();
            } else {
                player.pause();
                playPauseBtn.textContent = 'play';
                centerPlayBtn.classList.add('visible');
                showControls();
                clearTimeout(controlsTimeout);
            }
        }
        
        clickOverlay.addEventListener('click', function() {
            togglePlayPause();
        });
        
        centerPlayBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            togglePlayPause();
        });
        
        playPauseBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            togglePlayPause();
        });
        
        seekBack.addEventListener('click', function(e) {
            e.stopPropagation();
            player.currentTime(Math.max(0, player.currentTime() - 10));
            showControls();
            if (!player.paused()) hideControlsDelayed();
        });
        
        seekForward.addEventListener('click', function(e) {
            e.stopPropagation();
            player.currentTime(Math.min(player.duration() || 0, player.currentTime() + 10));
            showControls();
            if (!player.paused()) hideControlsDelayed();
        });
        
        fullscreenBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (!document.fullscreenElement) {
                videoWrapper.requestFullscreen?.();
            } else {
                document.exitFullscreen?.();
            }
        });
        
        let isDragging = false;
        progressTrack.addEventListener('mousedown', function(e) {
            isDragging = true;
            const rect = progressTrack.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            player.currentTime(pos * player.duration());
            progressFill.style.width = (pos * 100) + '%';
            progressTrack.classList.add('touching');
            e.preventDefault();
        });
        
        document.addEventListener('mousemove', function(e) {
            if (isDragging) {
                const rect = progressTrack.getBoundingClientRect();
                const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                player.currentTime(pos * player.duration());
                progressFill.style.width = (pos * 100) + '%';
            }
        });
        
        document.addEventListener('mouseup', function() {
            if (isDragging) {
                isDragging = false;
                progressTrack.classList.remove('touching');
                showControls();
                if (!player.paused()) hideControlsDelayed();
            }
        });
        
        progressTrack.addEventListener('touchstart', function(e) {
            const touch = e.touches[0];
            const rect = progressTrack.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width));
            player.currentTime(pos * player.duration());
            progressFill.style.width = (pos * 100) + '%';
            progressTrack.classList.add('touching');
            e.preventDefault();
        }, { passive: false });
        
        progressTrack.addEventListener('touchmove', function(e) {
            const touch = e.touches[0];
            const rect = progressTrack.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width));
            player.currentTime(pos * player.duration());
            progressFill.style.width = (pos * 100) + '%';
            e.preventDefault();
        }, { passive: false });
        
        progressTrack.addEventListener('touchend', function() {
            progressTrack.classList.remove('touching');
            showControls();
            if (!player.paused()) hideControlsDelayed();
        });
        
        player.on('timeupdate', updateTimeDisplay);
        player.on('loadedmetadata', updateTimeDisplay);
        player.on('play', function() {
            playPauseBtn.textContent = 'pause';
            centerPlayBtn.classList.remove('visible');
            showControls();
            hideControlsDelayed();
        });
        player.on('pause', function() {
            playPauseBtn.textContent = 'play';
            centerPlayBtn.classList.add('visible');
            showControls();
            clearTimeout(controlsTimeout);
        });
        player.on('ended', function() {
            playPauseBtn.textContent = 'play';
            centerPlayBtn.classList.add('visible');
            showControls();
            clearTimeout(controlsTimeout);
        });
        
        document.addEventListener('keydown', function(e) {
            if (e.key === ' ' || e.key === 'Space') { e.preventDefault(); togglePlayPause(); }
            if (e.key === 'ArrowLeft') { e.preventDefault(); seekBack.click(); }
            if (e.key === 'ArrowRight') { e.preventDefault(); seekForward.click(); }
            if (e.key === 'f' || e.key === 'F') { e.preventDefault(); fullscreenBtn.click(); }
        });
        
        clickOverlay.addEventListener('dblclick', function() {
            fullscreenBtn.click();
        });
        
        setTimeout(function() {
            showControls();
            if (player.paused()) {
                centerPlayBtn.classList.add('visible');
            } else {
                hideControlsDelayed();
            }
        }, 500);
        
        updateTimeDisplay();
    });
</script>
</body>
</html>
"""

TERABOX_PLAYER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Terabox Player</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            background: #000; 
            font-family: Arial, sans-serif;
            display: flex; 
            justify-content: center; 
            align-items: center; 
            height: 100vh; 
            overflow: hidden;
        }
        .container { 
            width: 100%;
            height: 100vh;
            background: #000;
            display: flex;
            flex-direction: column;
        }
        .video-wrapper {
            flex: 1;
            width: 100%;
            background: #000;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
            position: relative;
        }
        .video-wrapper iframe {
            width: 100%;
            height: 100%;
            border: none;
            background: #000;
        }
        .info {
            padding: 10px 16px;
            background: #111;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            border-top: 1px solid #1a1a1a;
            flex-shrink: 0;
        }
        .info .file-info {
            color: #555;
            font-size: 12px;
            font-family: Arial, sans-serif;
        }
        .info .file-info span { color: #888; }
        .back-btn {
            color: #00b4d8;
            text-decoration: none;
            padding: 4px 14px;
            border: 1px solid #00b4d8;
            border-radius: 20px;
            font-size: 12px;
            font-family: Arial, sans-serif;
            transition: all 0.3s;
        }
        .back-btn:hover { background: #00b4d8; color: #000; }
        .loading {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: #555;
            font-size: 14px;
            z-index: 5;
            text-align: center;
            font-family: Arial, sans-serif;
        }
        .loading .spinner {
            display: inline-block;
            width: 30px;
            height: 30px;
            border: 3px solid #222;
            border-top: 3px solid #00b4d8;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 10px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .save-toast {
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0,180,216,0.1);
            border: 1px solid rgba(0,180,216,0.05);
            padding: 0.4rem 1.2rem;
            border-radius: 30px;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.5rem;
            color: #00b4d8;
            opacity: 0;
            transition: all 0.5s ease;
            pointer-events: none;
            z-index: 100;
            backdrop-filter: blur(10px);
        }
        .save-toast.show {
            opacity: 1;
            bottom: 100px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="video-wrapper" id="videoWrapper">
            <div class="loading" id="loading">
                <div class="spinner"></div>
                <div>Loading player...</div>
            </div>
            <iframe 
                id="playerFrame"
                src="{{ video_url }}" 
                allowfullscreen 
                allow="autoplay; encrypted-media; fullscreen"
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"
                loading="eager"
            ></iframe>
        </div>
        <div class="info">
            <div class="file-info">
                📁 <span>{{ file_name }}</span>
                {% if file_size %}
                | 📦 <span>{{ file_size }}</span>
                {% endif %}
            </div>
            <a href="/" class="back-btn">← Back</a>
        </div>
    </div>

    <div class="save-toast" id="saveToast">📚 Added to library</div>

    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // ===== LIBRARY FUNCTIONS =====
            function getLibrary(platform) {
                try {
                    const key = platform === 'faphouse' ? 'faphouseLibrary' : 'teraboxLibrary';
                    return JSON.parse(localStorage.getItem(key) || '[]');
                } catch {
                    return [];
                }
            }
            
            function saveLibrary(platform, library) {
                const key = platform === 'faphouse' ? 'faphouseLibrary' : 'teraboxLibrary';
                localStorage.setItem(key, JSON.stringify(library));
            }
            
            function addToLibrary(platform, video) {
                const library = getLibrary(platform);
                const exists = library.some(item => item.url === video.url);
                if (!exists) {
                    video.watchedAt = new Date().toISOString();
                    library.unshift(video);
                    saveLibrary(platform, library);
                    showToast('📚 Added to ' + platform + ' library');
                    return true;
                }
                return false;
            }
            
            function showToast(message) {
                const toast = document.getElementById('saveToast');
                toast.textContent = message;
                toast.classList.add('show');
                setTimeout(() => {
                    toast.classList.remove('show');
                }, 3000);
            }
            
            // ===== SAVE VIDEO TO LIBRARY =====
            const videoUrl = "{{ video_url }}";
            const originalUrl = new URLSearchParams(window.location.search).get('url') || '';
            const fileName = "{{ file_name }}" || 'Terabox Video';
            
            let saved = false;
            
            const iframe = document.getElementById('playerFrame');
            const loading = document.getElementById('loading');
            
            iframe.addEventListener('load', function() {
                loading.style.display = 'none';
                
                if (!saved && (videoUrl || originalUrl)) {
                    saved = true;
                    addToLibrary('terabox', {
                        url: originalUrl || videoUrl,
                        title: fileName,
                        platform: 'terabox',
                        videoUrl: videoUrl,
                        file_name: fileName,
                        file_size: "{{ file_size }}"
                    });
                }
            });
            
            setTimeout(function() {
                loading.style.display = 'none';
                if (!saved && (videoUrl || originalUrl)) {
                    saved = true;
                    addToLibrary('terabox', {
                        url: originalUrl || videoUrl,
                        title: fileName,
                        platform: 'terabox',
                        videoUrl: videoUrl,
                        file_name: fileName,
                        file_size: "{{ file_size }}"
                    });
                }
            }, 8000);
        });
    </script>
</body>
</html>
"""

ERROR_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            background: #0a0a0a; 
            font-family: Arial, sans-serif;
            display: flex; 
            justify-content: center; 
            align-items: center; 
            min-height: 100vh;
            padding: 20px;
        }
        .error-container {
            max-width: 500px;
            width: 100%;
            padding: 40px;
            background: #111;
            border-radius: 16px;
            border: 1px solid #222;
            text-align: center;
        }
        .error-icon {
            font-size: 48px;
            margin-bottom: 20px;
        }
        .error-title {
            color: #ff4444;
            font-size: 20px;
            font-weight: bold;
            margin-bottom: 12px;
        }
        .error-message {
            color: #888;
            font-size: 14px;
            line-height: 1.6;
            margin-bottom: 20px;
        }
        .error-message .highlight {
            color: #00b4d8;
        }
        .back-btn {
            display: inline-block;
            padding: 10px 30px;
            background: #00b4d8;
            color: #000;
            text-decoration: none;
            border-radius: 8px;
            font-weight: bold;
            transition: all 0.3s;
        }
        .back-btn:hover {
            background: #48cae4;
            transform: scale(0.98);
        }
        .error-details {
            margin-top: 15px;
            padding: 10px;
            background: #1a1a1a;
            border-radius: 8px;
            font-size: 12px;
            color: #555;
            word-break: break-all;
        }
        .error-details .label {
            color: #444;
            font-weight: bold;
        }
        .error-details .value {
            color: #777;
        }
    </style>
</head>
<body>
    <div class="error-container">
        <div class="error-icon">❌</div>
        <div class="error-title">{{ error_title }}</div>
        <div class="error-message">{{ error_message }}</div>
        <a href="/" class="back-btn">← Go Home</a>
        {% if error_detail %}
        <div class="error-details">
            <span class="label">Details:</span>
            <span class="value">{{ error_detail }}</span>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

# ============= ROUTES =============

@app.route('/proxy/m3u8')
def proxy_m3u8():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    url = unquote(url)
    try:
        content = faphouse_client.fetch_m3u8_proxied(url)
        if content:
            return Response(content, content_type='application/vnd.apple.mpegurl', headers={
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'public, max-age=10'
            })
        return Response("Not found", status=404)
    except Exception as e:
        logger.error(f"Proxy m3u8 error: {e}")
        return Response("Error", status=500)

@app.route('/proxy/media')
def proxy_media():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    url = unquote(url)
    range_header = request.headers.get('Range')
    return faphouse_client.fetch_media_proxied(url, range_header)

@app.route('/')
def index():
    return render_template_string(MAIN_PAGE_HTML, video_url=None)

@app.route('/play')
def play_video():
    video_url = request.args.get('url')
    
    if not video_url:
        return render_template_string(MAIN_PAGE_HTML, video_url=None)
    
    if '#' in video_url:
        video_url = video_url.split('#')[0]
    
    try:
        logger.info(f"Faphouse play request for: {video_url}")
        m3u8_url = faphouse_client.get_m3u8_url(video_url)
        
        if m3u8_url:
            proxy_m3u8_url = f"/proxy/m3u8?url={quote(m3u8_url, safe='')}"
            return render_template_string(
                PLAYER_PAGE_HTML,
                m3u8_url=proxy_m3u8_url,
                platform="faphouse",
                file_name="",
                file_size=""
            )
        else:
            return render_template_string(
                ERROR_PAGE_HTML,
                error_title="Video Not Found",
                error_message="Could not find a playable video URL. The video might be unavailable, private, or removed.",
                error_detail="No M3U8 URL found in the page source"
            )
    except Exception as e:
        logger.error(f"Play error: {str(e)}")
        return render_template_string(
            ERROR_PAGE_HTML,
            error_title="Something Went Wrong",
            error_message="An unexpected error occurred while trying to play this video.",
            error_detail=str(e)
        )

@app.route('/terabox')
def terabox_player():
    video_url = request.args.get('url')
    
    if not video_url:
        return render_template_string(MAIN_PAGE_HTML, video_url=None)
    
    try:
        logger.info(f"Terabox request for: {video_url}")
        result = terabox_client.process_terabox_link(video_url)
        
        if result.get('error'):
            return render_template_string(
                ERROR_PAGE_HTML,
                error_title="Terabox Error",
                error_message=result['error'],
                error_detail=""
            )
        
        return render_template_string(
            TERABOX_PLAYER_HTML,
            video_url=result['video_url'],
            file_name=result.get('file_name', ''),
            file_size=result.get('file_size', '')
        )
        
    except Exception as e:
        logger.error(f"Terabox error: {str(e)}")
        return render_template_string(
            ERROR_PAGE_HTML,
            error_title="Something Went Wrong",
            error_message="An unexpected error occurred while processing your request.",
            error_detail=str(e)
        )

@app.route('/api/m3u8')
def get_m3u8():
    video_url = request.args.get('url')
    
    if not video_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    
    try:
        if '#' in video_url:
            video_url = video_url.split('#')[0]
            
        m3u8_url = faphouse_client.get_m3u8_url(video_url)
        
        if m3u8_url:
            proxy_m3u8_url = f"/proxy/m3u8?url={quote(m3u8_url, safe='')}"
            return jsonify({
                "success": True,
                "m3u8_url": m3u8_url,
                "proxy_m3u8_url": proxy_m3u8_url,
                "video_url": video_url,
                "platform": "faphouse"
            })
        else:
            return jsonify({
                "success": False,
                "error": "No M3U8 URL found"
            }), 404
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/terabox')
def api_terabox():
    video_url = request.args.get('url')
    
    if not video_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    
    try:
        result = terabox_client.process_terabox_link(video_url)
        
        if result.get('error'):
            return jsonify({"success": False, "error": result['error']}), 404
        
        return jsonify({
            "success": True,
            "video_url": result['video_url'],
            "file_name": result.get('file_name', ''),
            "file_size": result.get('file_size', ''),
            "platform": "terabox"
        })
        
    except Exception as e:
        logger.error(f"API Terabox error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/status')
def status():
    return jsonify({
        "status": "online",
        "faphouse": {
            "logged_in": faphouse_client.logged_in,
            "session_created": faphouse_client.session_created,
            "cache_size": len(faphouse_client._m3u8_cache)
        },
        "terabox": {
            "cache_size": len(terabox_client.cache)
        }
    })

def handler(request, context):
    return app(request.environ, context)

if __name__ == "__main__":
    print(f"""
{'='*70}
Faphouse + Terabox Player API with Collapsible Library
{'='*70}

Features:
  • Faphouse: Logs in and extracts M3U8 URLs (Video.js player)
  • Terabox: Extracts proxy URL and embeds in iframe
  • 📚 Collapsible library sidebar (hamburger menu button)
  • 📝 Separate libraries for each platform
  • 🔄 Click library items to replay videos
  • ❌ Remove individual videos or clear library per platform
  • 🔴 Live badge count on library button
  • Premium 18+ webplayer UI with dual platform support

Endpoints:
  /play?url=URL         - Faphouse video player (Video.js)
  /terabox?url=URL      - Terabox video player (iframe)
  /api/m3u8?url=URL     - Get Faphouse M3U8 URL
  /api/terabox?url=URL  - Get Terabox video URL
  /api/status           - Check status

Faphouse Credentials:
  EMAIL: {EMAIL[:5]}... 
  PASSWORD: {'*' * 8}
{'='*70}
""")
    
    print("Starting server for local testing...")
    print("Try this Terabox link: https://terafileshare.com/s/1xJtL3j2LJ-ZsUA6zbG7Pug")
    app.run(host='0.0.0.0', port=5000, debug=True)
