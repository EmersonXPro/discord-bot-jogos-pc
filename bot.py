import sys
import types

# Patch CRÍTICO para evitar erro do audioop no Python 3.13+
# Deve ser executado ANTES de importar o discord.py
if sys.version_info >= (3, 13) or "audioop" not in sys.modules:
    try:
        import audioop
    except ImportError:
        audioop_mock = types.ModuleType("audioop")
        # Adicionar funções vazias que o discord.py pode tentar usar
        audioop_mock.mul = lambda data, size, factor: data
        audioop_mock.tomono = lambda data, size, lfactor, rfactor: data
        audioop_mock.max = lambda data, size: 0
        sys.modules["audioop"] = audioop_mock

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from bs4 import BeautifulSoup
import asyncio
import re
import logging
import os

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# O TOKEN agora é lido de uma variável de ambiente por segurança
TOKEN = os.getenv("DISCORD_TOKEN")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ──────────────────────────────────────────────────────────────────────────────
# SCRAPING: repack-games.com
# ──────────────────────────────────────────────────────────────────────────────

async def search_repackgames(session: aiohttp.ClientSession, game_name: str):
    """Busca o jogo no repack-games.com e retorna dados estruturados."""
    search_url = f"https://repack-games.com/?s={game_name.replace(' ', '+')}"
    try:
        async with session.get(search_url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception as e:
        logger.error(f"Erro ao buscar repack-games: {e}")
        return None

    soup = BeautifulSoup(html, "lxml")

    # Encontrar o primeiro resultado de busca
    article_link = None
    for a in soup.select("article a, .post-title a, h2.entry-title a, h3.entry-title a, .entry-title a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href and text and "free-download" in href.lower():
            article_link = href
            break

    # Fallback: qualquer link de artigo
    if not article_link:
        for a in soup.select("a[href*='repack-games.com']"):
            href = a.get("href", "")
            if "free-download" in href.lower() and "repack-games.com" in href:
                article_link = href
                break

    if not article_link:
        return None

    # Acessar a página do jogo
    try:
        async with session.get(article_link, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            game_html = await resp.text()
    except Exception as e:
        logger.error(f"Erro ao acessar página do jogo repack-games: {e}")
        return None

    return parse_repackgames(game_html, article_link)


def parse_repackgames(html: str, page_url: str):
    """Extrai informações da página de um jogo no repack-games.com."""
    soup = BeautifulSoup(html, "lxml")

    # Título
    title_tag = soup.find("h1", class_=re.compile(r"entry-title|post-title|title"))
    if not title_tag:
        title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Título não encontrado"
    # Limpar "Free Download" do título
    title = re.sub(r'\s*Free Download.*$', '', title, flags=re.IGNORECASE).strip()

    # Imagem de capa
    image_url = None
    img_tag = soup.find("img", class_=re.compile(r"wp-post-image|attachment|featured"))
    if not img_tag:
        # Tentar pegar a primeira imagem grande do conteúdo
        content_div = soup.find("div", class_=re.compile(r"entry-content|post-content"))
        if content_div:
            img_tag = content_div.find("img")
    if img_tag:
        image_url = img_tag.get("src") or img_tag.get("data-src")

    # Links de download
    download_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).upper()
        if any(host in href for host in ["buzzheavier", "filecrypt", "datanodes", "qiwi", "1fichier", "gofile", "pixeldrain", "mediafire", "mega.nz"]):
            download_links.append({"label": text or "Download", "url": href})

    # Também pegar botões "Download Here"
    if not download_links:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "download" in text.lower() and href.startswith("http") and "repack-games.com" not in href:
                download_links.append({"label": text, "url": href})

    # Requisitos do sistema
    sys_req = extract_system_requirements(soup)

    # Informações do jogo
    game_info = extract_game_info_repack(soup)

    return {
        "source": "repack-games.com",
        "title": title,
        "url": page_url,
        "image_url": image_url,
        "download_links": download_links,
        "sys_req": sys_req,
        "game_info": game_info,
    }


def extract_system_requirements(soup: BeautifulSoup) -> dict:
    """Extrai requisitos do sistema de qualquer estrutura HTML."""
    req = {
        "os": None,
        "processor": None,
        "memory": None,
        "graphics": None,
        "directx": None,
        "storage": None,
    }

    # Procurar seção de requisitos
    text_content = soup.get_text("\n")
    lines = text_content.split("\n")

    patterns = {
        "os": re.compile(r"(?:OS|Sistema Operacional|Operating System)[:\s]+(.+)", re.IGNORECASE),
        "processor": re.compile(r"(?:Processor|CPU|Processador)[:\s]+(.+)", re.IGNORECASE),
        "memory": re.compile(r"(?:Memory|RAM|Memória)[:\s]+(.+)", re.IGNORECASE),
        "graphics": re.compile(r"(?:Graphics|GPU|Placa\s*[Gg]ráfica|Video Card)[:\s]+(.+)", re.IGNORECASE),
        "directx": re.compile(r"(?:DirectX)[:\s]+(.+)", re.IGNORECASE),
        "storage": re.compile(r"(?:Storage|Disk Space|Armazenamento|HDD|SSD)[:\s]+(.+)", re.IGNORECASE),
    }

    for line in lines:
        line = line.strip()
        for key, pattern in patterns.items():
            if req[key] is None:
                m = pattern.match(line)
                if m:
                    val = m.group(1).strip()
                    if val and len(val) < 200:
                        req[key] = val

    return req


def extract_game_info_repack(soup: BeautifulSoup) -> dict:
    """Extrai informações do jogo do repack-games."""
    info = {
        "genre": None,
        "developer": None,
        "platform": "PC",
        "size": None,
        "released_by": None,
        "version": None,
    }

    text_content = soup.get_text("\n")
    lines = text_content.split("\n")

    # Procurar "Game size" e "Cracked By"
    for line in lines:
        line = line.strip()
        m = re.search(r"Game\s*[Ss]ize[:\s]+([^\n]+)", line, re.IGNORECASE)
        if m and not info["size"]:
            info["size"] = m.group(1).strip()

        m = re.search(r"(?:Cracked\s*By|Released\s*By)[:\s]+([^\n]+)", line, re.IGNORECASE)
        if m and not info["released_by"]:
            info["released_by"] = m.group(1).strip()

        m = re.search(r"Game[:\s]+V?\s*([\d.]+)", line, re.IGNORECASE)
        if m and not info["version"]:
            info["version"] = m.group(1).strip()

    # Gênero a partir das categorias/tags
    genre_tags = soup.select("a[rel='category tag'], .entry-categories a, .post-categories a, .tags a")
    genres = [t.get_text(strip=True) for t in genre_tags if t.get_text(strip=True)]
    if genres:
        info["genre"] = ", ".join(genres[:3])

    return info


# ──────────────────────────────────────────────────────────────────────────────
# SCRAPING: steamrip.com
# ──────────────────────────────────────────────────────────────────────────────

async def search_steamrip(session: aiohttp.ClientSession, game_name: str):
    """Busca o jogo no steamrip.com e retorna dados estruturados."""
    search_url = f"https://steamrip.com/?s={game_name.replace(' ', '+')}"
    try:
        async with session.get(search_url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception as e:
        logger.error(f"Erro ao buscar steamrip: {e}")
        return None

    soup = BeautifulSoup(html, "lxml")
    BASE_URL = "https://steamrip.com/"

    # Encontrar o primeiro resultado — steamrip usa URLs relativas
    article_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        # URL relativa ou absoluta com free-download
        if "free-download" in href.lower() and text:
            if href.startswith("http"):
                article_link = href
            else:
                article_link = BASE_URL + href.lstrip("/")
            break

    if not article_link:
        return None

    # Acessar a página do jogo
    try:
        async with session.get(article_link, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            game_html = await resp.text()
    except Exception as e:
        logger.error(f"Erro ao acessar página do jogo steamrip: {e}")
        return None

    return parse_steamrip(game_html, article_link)


def parse_steamrip(html: str, page_url: str):
    """Extrai informações da página de um jogo no steamrip.com."""
    soup = BeautifulSoup(html, "lxml")

    # Título
    title_tag = soup.find("h1", class_=re.compile(r"post-title|entry-title"))
    if not title_tag:
        title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Título não encontrado"
    title = re.sub(r'\s*Free Download.*$', '', title, flags=re.IGNORECASE).strip()

    # Imagem de capa
    image_url = None
    img_tag = soup.find("img", class_=re.compile(r"wp-post-image|featured|attachment"))
    if not img_tag:
        content_div = soup.find("div", class_=re.compile(r"entry-content|post-content|article-content"))
        if content_div:
            img_tag = content_div.find("img")
    if img_tag:
        image_url = img_tag.get("src") or img_tag.get("data-src")

    # Links de download (SteamRIP usa botões com links específicos)
    download_links = []
    
    # Método Fallback Regex para links de download (SteamRIP)
    patterns = [
        r'href=["\'](https?:)?//(buzzheavier\.com/[^"\']+)["\']',
        r'href=["\'](https?:)?//(gofile\.io/[^"\']+)["\']',
        r'href=["\'](https?:)?//(qiwi\.gg/[^"\']+)["\']',
        r'href=["\'](https?:)?//(megaup\.net/[^"\']+)["\']',
        r'href=["\'](https?:)?//(1fichier\.com/[^"\']+)["\']',
        r'href=["\'](https?:)?//(pixeldrain\.com/[^"\']+)["\']',
    ]
    
    found_urls = set()
    for pattern in patterns:
        matches = re.finditer(pattern, html, re.IGNORECASE)
        for m in matches:
            url = m.group(2)
            if not url.startswith("http"):
                url = "https://" + url
            if url not in found_urls:
                download_links.append({"label": "Download Link", "url": url})
                found_urls.add(url)

    # Requisitos do sistema
    sys_req = extract_system_requirements(soup)

    # Informações do jogo
    game_info = extract_game_info_steamrip(soup)

    return {
        "source": "steamrip.com",
        "title": title,
        "url": page_url,
        "image_url": image_url,
        "download_links": download_links,
        "sys_req": sys_req,
        "game_info": game_info,
    }


def extract_game_info_steamrip(soup: BeautifulSoup) -> dict:
    """Extrai informações do jogo do steamrip."""
    info = {
        "genre": None,
        "developer": None,
        "platform": "PC",
        "size": None,
        "released_by": "SteamRIP",
        "version": None,
    }

    # SteamRIP geralmente coloca informações no topo do post
    content = soup.find("div", class_=re.compile(r"entry-content|post-content"))
    if content:
        text = content.get_text("\n")
        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if "Genre:" in line:
                info["genre"] = line.replace("Genre:", "").strip()
            elif "Developer:" in line:
                info["developer"] = line.replace("Developer:", "").strip()
            elif "Size:" in line:
                info["size"] = line.replace("Size:", "").strip()
            elif "Version:" in line:
                info["version"] = line.replace("Version:", "").strip()

    return info


# ──────────────────────────────────────────────────────────────────────────────
# BOT COMMANDS
# ──────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(f"Bot logado como {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync()
        logger.info(f"Sincronizados {len(synced)} comandos slash.")
    except Exception as e:
        logger.error(f"Erro ao sincronizar comandos: {e}")


@tree.command(name="jogos", description="Busca links de download para um jogo de PC")
@app_commands.describe(pc="Nome do jogo que você quer encontrar")
async def jogos(interaction: discord.Interaction, pc: str):
    # Usar defer para evitar timeout do Discord (máximo 3 segundos)
    await interaction.response.defer(thinking=True)
    
    logger.info(f"Usuário {interaction.user} buscou por: {pc}")

    try:
        async with aiohttp.ClientSession() as session:
            # Busca paralela nos dois sites
            results = await asyncio.gather(
                search_repackgames(session, pc),
                search_steamrip(session, pc),
                return_exceptions=True
            )

        # Filtrar resultados válidos
        valid_results = [r for r in results if isinstance(r, dict) and r.get("download_links")]

        if not valid_results:
            await interaction.followup.send(f"❌ Não encontrei nenhum link de download para **{pc}** nos sites monitorados.")
            return

        # Pegar o melhor resultado
        res = valid_results[0]
        
        # Montar a resposta conforme o modelo solicitado
        title = res['title']
        links = res['download_links']
        primary = links[0]['url'] if len(links) > 0 else "Não disponível"
        secondary = links[1]['url'] if len(links) > 1 else "Não disponível"
        
        req = res['sys_req']
        info = res['game_info']
        
        response_text = f"""
**Jogo: {title}**

**Link Primário:** {primary}
**Link secundário:** {secondary}

**REQUISITOS DO SISTEMA**

**Sistema Operacional:** {req['os'] or 'N/A'}
**Processador:** {req['processor'] or 'N/A'}
**Memória:** {req['memory'] or 'N/A'}
**Placa gráfica:** {req['graphics'] or 'N/A'}
**DirectX:** {req['directx'] or 'N/A'}
**Armazenamento:** {req['storage'] or 'N/A'}

**INFORMAÇÕES DO JOGO**
**Gênero:** {info['genre'] or 'N/A'}
**Desenvolvedora:** {info['developer'] or 'N/A'}
**Plataforma:** {info['platform']}
**Tamanho do jogo:** {info['size'] or 'N/A'}
**Lançado por:** {info['released_by'] or 'N/A'}
**Versão:** {info['version'] or 'N/A'}
**Jogo pré-instalado**

**Método de Instalação:**
    Após baixar o arquivo do jogo através dos links acima, em seu PC extraia o arquivo usando um gerenciador de arquivos, recomendo o WinRAR. Dentro da pasta do jogo, Procurar o Executável do Jogo que geralmente é o nome do jogo com .Exe no final, execute como Administrador - Pronto, seja feliz.
"""
        
        embed = discord.Embed(description=response_text, color=discord.Color.blue())
        if res['image_url']:
            embed.set_image(url=res['image_url'])
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Erro ao processar comando /jogos: {e}")
        try:
            await interaction.followup.send("❌ Ocorreu um erro ao processar sua busca. Tente novamente mais tarde.")
        except:
            pass


# Servidor Keep-Alive para o Render (Web Service)
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    # Render usa a porta da variável de ambiente PORT
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Iniciando servidor Flask na porta {port}...")
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    if TOKEN:
        keep_alive()
        try:
            bot.run(TOKEN)
        except Exception as e:
            logger.error(f"Erro fatal ao rodar o bot: {e}")
    else:
        logger.error("Erro: DISCORD_TOKEN não encontrado nas variáveis de ambiente.")
