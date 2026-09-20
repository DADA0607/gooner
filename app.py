import requests
import re
import json
import urllib.parse
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class TeraboxDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False  
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-MM,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'Sec-Ch-Ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'Sec-Ch-Ua-Mobile': '?1',
            'Sec-Ch-Ua-Platform': '"Android"'
        }
        self.base_url = "https://terabox.beer"
        
    def extract_video_id(self, url):
        patterns = [
            r'/s/([a-zA-Z0-9_-]+)',
            r'share\.com/s/([a-zA-Z0-9_-]+)',
            r'file\.com/s/([a-zA-Z0-9_-]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def extract_m3u8_url(self, text):
        patterns = [
            r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+/playlist\.m3u8[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+\.m3u8\?[^\s"\'<>]*)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def follow_redirects(self, url, max_redirects=5):
        current_url = url
        redirect_count = 0
        
        while redirect_count < max_redirects:
            try:
                response = self.session.get(
                    current_url,
                    headers=self.headers | {'Referer': self.base_url + '/'},
                    allow_redirects=False,
                    timeout=30
                )
                
                if response.status_code in [301, 302, 303, 307, 308]:
                    location = response.headers.get('Location')
                    if location:
                        if location.startswith('/'):
                            parsed = urllib.parse.urlparse(current_url)
                            location = f"{parsed.scheme}://{parsed.netloc}{location}"
                        elif not location.startswith('http'):
                            parsed = urllib.parse.urlparse(current_url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            if not location.startswith('/'):
                                base += '/' + '/'.join(parsed.path.split('/')[:-1])
                            location = base + '/' + location.lstrip('/')
                        
                        print(f"↪️ Redirect {redirect_count + 1}: {location}")
                        current_url = location
                        redirect_count += 1
                        continue
                if response.text:
                    m3u8_url = self.extract_m3u8_url(response.text)
                    if m3u8_url:
                        print(f"🎬 Found M3U8 URL: {m3u8_url}")
                        return {
                            'final_url': current_url,
                            'm3u8_url': m3u8_url,
                            'response': response
                        }
                
                return {
                    'final_url': current_url,
                    'm3u8_url': None,
                    'response': response
                }
                
            except Exception as e:
                print(f"⚠️ Error following redirects: {str(e)}")
                return {
                    'final_url': current_url,
                    'm3u8_url': None,
                    'response': None
                }
        
        return {
            'final_url': current_url,
            'm3u8_url': None,
            'response': None
        }

    def process_terabox_link(self, terabox_url):
        video_id = self.extract_video_id(terabox_url)
        if not video_id:
            return {"error": "Could not extract video ID from the link"}
        
        print(f"📌 Extracted Video ID: {video_id}")

        print("🌐 Making initial connection...")
        response = self.session.get(
            self.base_url,
            headers=self.headers | {'Referer': 'https://www.google.com/'}
        )

        watch_url = f"{self.base_url}/watch/{video_id}"
        print(f"📺 Accessing watch page: {watch_url}")
        response = self.session.get(
            watch_url,
            headers=self.headers | {'Referer': self.base_url + '/'}
        )
        
        if 'TeraBox Video Player' in response.text:
            print("✅ Sent To Api")
        encoded_url = urllib.parse.quote(terabox_url, safe='')
        api_url = f"{self.base_url}/api/terabox-new?link={encoded_url}"
        print("📡 Fetching video information...")
        
        response = self.session.get(
            api_url,
            headers=self.headers | {'Referer': watch_url}
        )
        
        try:
            api_result = response.json()
        except:
            return {"error": "Failed to parse API response"}
        
        if isinstance(api_result, dict):
            if api_result.get('error') == False:
                print("✅ API returned success!")

                video_url = None
                possible_fields = [
                    'stream_download_url',
                    'download_link', 
                    'fallback_url',
                    'proxy_url',
                    'url',
                    'video_url'
                ]
                
                for field in possible_fields:
                    if field in api_result and api_result[field]:
                        video_url = api_result[field]
                        print(f"✅ Found video URL in field '{field}'")
                        break
                
                if not video_url:
                    for key, value in api_result.items():
                        if isinstance(value, str) and (value.startswith('http://') or value.startswith('https://')):
                            video_url = value
                            print(f"✅ Found URL in field '{key}'")
                            break
                
                if not video_url:
                    return {"error": "No video URL found in API response"}
                
                file_name = api_result.get('file_name', 'Unknown')
                file_size = api_result.get('file_size', 'Unknown')
                print(f"📁 File Name: {file_name}")
                print(f"📦 File Size: {file_size}")
                
            else:
                error_msg = api_result.get('error') or api_result.get('message') or 'Unknown error'
                return {"error": f"API request failed: {error_msg}"}
        else:
            return {"error": "API response is not JSON"}
        print("🔄 Following redirects to find actual video URL...")
        redirect_result = self.follow_redirects(video_url)
        if redirect_result['m3u8_url']:
            final_video_url = redirect_result['m3u8_url']
            print("✅ Found M3U8 streaming URL!")
        else:
            final_video_url = video_url
            print("ℹ️ Using original video URL (no m3u8 found)")
        
        return {
            "success": True,
            "video_id": video_id,
            "video_url": final_video_url,
            "original_url": video_url,
            "watch_page": watch_url,
            "file_name": file_name,
            "file_size": file_size
        }

def main():
    print("=" * 30)
    print("🎬 TeraBox URL To Video")
    print("=" * 30)
    print()
    print("📌 This tool extracts video URLs from TeraBox links")
    print("📌 Supports: terabox.com, teraboxlink.com, 1024terabox.com, etc.")
    print()
    terabox_link = input("📎 Enter your Terabox link: ").strip()
    
    if not terabox_link:
        print("❌ No link provided!")
        return
    downloader = TeraboxDownloader()
    
    print("\n🔄 Processing your link...\n")
    result = downloader.process_terabox_link(terabox_link)
    
    if result.get("error"):
        print(f"\n❌ Error: {result['error']}")
        return
    print("\n" + "=" * 60)
    print("✅ SUCCESS! Video details:")
    print("=" * 60)
    print(f"📌 Video ID: {result['video_id']}")
    print(f"📁 File Name: {result.get('file_name', 'Unknown')}")
    print(f"📦 File Size: {result.get('file_size', 'Unknown')}")
    print(f"🎯 Streaming URL: {result['video_url']}")
    print("\n" + "=" * 60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        print("\n💡 Make sure you have an internet connection")
        print("💡 Try again with a valid Terabox link")