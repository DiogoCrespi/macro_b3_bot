import sys
import xml.etree.ElementTree as ET
import urllib.request
from pathlib import Path
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

class NewsScraper:
    """
    Scraper e agregador de noticias macroeconomicas e geopoliticas via RSS.
    """
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        self.rss_feeds = [
            "https://news.google.com/rss/search?q=economia+brasil+commodities+fed+selic&hl=pt-BR&gl=BR&ceid=BR:pt-419",
            "https://news.google.com/rss/search?q=el+nino+agronegocio+energia+b3&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        ]

    def fetch_rss_articles(self, max_items_per_feed: int = 5) -> List[Dict[str, str]]:
        """Busca ultimas noticias dos feeds RSS configurados."""
        articles = []
        for feed_url in self.rss_feeds:
            try:
                req = urllib.request.Request(feed_url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=8) as response:
                    xml_data = response.read()
                
                root = ET.fromstring(xml_data)
                count = 0
                for item in root.findall("./channel/item"):
                    if count >= max_items_per_feed:
                        break
                    title = item.findtext("title", default="")
                    link = item.findtext("link", default="")
                    pub_date = item.findtext("pubDate", default="")
                    
                    articles.append({
                        "title": title,
                        "link": link,
                        "pub_date": pub_date
                    })
                    count += 1
            except Exception as e:
                # Log e continua pro proximo feed
                continue
        return articles

if __name__ == "__main__":
    scraper = NewsScraper()
    news = scraper.fetch_rss_articles(3)
    print(f"Total de noticias coletadas: {len(news)}")
    for n in news:
        print(f" - {n['title']}")
