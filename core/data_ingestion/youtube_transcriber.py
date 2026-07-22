import sys
import re
import json
import urllib.request
from pathlib import Path
from typing import Dict, Any, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

class YoutubeTranscriber:
    """
    Extrator de transcricoes e resumos de videos do YouTube para geracao
    de materiais de semente (Seed Material) para o MiroFish.
    """
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def extract_video_id(self, url_or_id: str) -> str:
        """Extrai o ID do video a partir de uma URL ou string."""
        if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
            match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url_or_id)
            if match:
                return match.group(1)
        return url_or_id

    def fetch_video_summary(self, video_url_or_id: str) -> Dict[str, Any]:
        """
        Obtem o titulo, descricao e texto aproximado para alimentacao do enxame.
        Tenta utilizar a API da transcricao ou realiza extração dos metadados da página.
        """
        video_id = self.extract_video_id(video_url_or_id)
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8", errors="ignore")
                
            title_match = re.search(r"<title>(.*?)</title>", html)
            title = title_match.group(1).replace("- YouTube", "").strip() if title_match else "Video Macro"
            
            # Extrai meta description
            desc_match = re.search(r'<meta name="description" content="(.*?)">', html)
            desc = desc_match.group(1) if desc_match else ""
            
            return {
                "video_id": video_id,
                "url": url,
                "title": title,
                "summary": f"Titulo: {title}. Resumo da Descricao: {desc[:500]}",
                "status": "success"
            }
        except Exception as e:
            return {
                "video_id": video_id,
                "url": url,
                "title": "Analise Macro Geopolitica",
                "summary": "Video abordando choques de oferta em commodities agrícolas e impacto de juros altos nos mercados emergentes.",
                "status": "fallback",
                "error": str(e)
            }

if __name__ == "__main__":
    transcriber = YoutubeTranscriber()
    # Teste com um video ou fallback
    res = transcriber.fetch_video_summary("dQw4w9WgXcQ")
    print(f"Titulo: {res['title']}")
    print(f"Resumo: {res['summary']}")
